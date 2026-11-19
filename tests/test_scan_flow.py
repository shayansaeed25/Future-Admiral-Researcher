"""End-to-end hourly scan on synthetic candles: signal -> monitor -> SL/TP -> sheet sync -> anti-doubling."""
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import backtest as bt
import cloud_run
import engine_state
import etf_flow
import google_sheets_sync as gs
import macro
import market_data as md
from tests.synth_data import synth

I = 3389  # synthetic bar where ETH (seed 14) has an A-grade pullback BUY


class FakeMarket:
    def __init__(self, bars=I):
        self.series = {"BTC-USDT": synth(4400, 1, 80000), "ETH-USDT": synth(4400, 14, 2500), "SOL-USDT": synth(4400, 3, 110)}
        self.prefix = {t: {k: v[: bars + 1] for k, v in s.items()} for t, s in self.series.items()}
        self.range = None
        self.price_shift = 1.0

    def fetch_candles(self, ticker, tf, limit=300, closed_only=True):
        p = self.prefix[ticker]
        if tf == "1h":
            return p
        return bt.resample(p, 4 * 3600, 0) if tf == "4h" else bt.resample(p, 86400, 8 * 3600)

    def get_prices(self, tickers):
        return {t: float(self.prefix[t]["c"][-1]) * self.price_shift for t in tickers if t in self.prefix}

    def get_range_since(self, ticker, since_ms):
        if self.range:
            return {"low": self.range[0], "high": self.range[1], "candles": 60, "source": "mock"}
        px = float(self.prefix[ticker]["c"][-1])
        return {"low": px, "high": px, "candles": 60, "source": "mock"}


class ScanFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cwd = os.getcwd()
        os.chdir(self.tmp.name)
        with open("watchlist.json", "w", encoding="utf-8") as fh:
            json.dump({"coins": [{"ticker": t, "interval_hours": 1} for t in ("BTC-USDT", "ETH-USDT", "SOL-USDT")]}, fh)
        self.fm = FakeMarket()
        self.embeds = []
        last_close = (self.fm.prefix["ETH-USDT"]["ts"][-1] + 3_600_000 + 600_000) / 1000
        patches = [
            mock.patch.object(md, "fetch_candles", self.fm.fetch_candles),
            mock.patch.object(md, "get_prices", self.fm.get_prices),
            mock.patch.object(md, "get_price", lambda t: self.fm.get_prices([t]).get(t)),
            mock.patch.object(md, "get_funding_pct", lambda t: 0.01),
            mock.patch.object(md, "get_oi_change_pct", lambda t: 0.6),
            mock.patch.object(md, "get_range_since", self.fm.get_range_since),
            mock.patch.object(etf_flow, "get_institutional_flow", lambda t: {}),
            mock.patch.object(md, "get_taker_flow", lambda t: None),
            mock.patch.object(md, "get_coinbase_premium_pct", lambda t, p: None),
            mock.patch.object(macro, "get_macro", lambda: {"risk": 0, "notes": [], "blackout": False}),
            mock.patch.object(cloud_run, "send_discord", lambda e: self.embeds.append(e) or True),
            mock.patch.object(cloud_run, "HEARTBEAT_HOUR_PKT", 99),
            mock.patch("time.time", lambda: last_close),
            mock.patch.dict(os.environ, {"ADMIRAL_LLM_VERDICT": "0", "ADMIRAL_SETUP_TF": "1", "ADMIRAL_SETUPS": "pullback,breakout", "ADMIRAL_ADX_MIN": "20", "ADMIRAL_MIN_SCORE": "62"}),
            mock.patch.object(cloud_run, "MAX_HOLD_HOURS", 72.0),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        os.chdir(self.cwd)
        self.tmp.cleanup()

    def positions(self):
        with open("active_signals.json", "r", encoding="utf-8") as fh:
            return json.load(fh)

    def titles(self):
        return [e.get("title", "") for e in self.embeds]

    def test_full_lifecycle(self):
        cloud_run.main()
        pos = list(self.positions().values())
        eth = [p for p in pos if p["ticker"] == "ETH-USDT"]
        self.assertEqual(len(eth), 1, self.titles())
        p = eth[0]
        self.assertEqual(p["action"], "BUY")
        self.assertLess(p["sl"], p["entry"])
        self.assertLess(p["entry"], p["tp1"])
        self.assertLess(p["tp1"], p["tp2"])
        self.assertLess(p["tp2"], p["tp3"])
        self.assertTrue(any("ETH-USDT — BUY" in t for t in self.titles()))
        self.assertFalse(any("NO SIGNAL" in json.dumps(e) for e in self.embeds))
        n_embeds = len(self.embeds)

        cloud_run.main()
        self.assertEqual(len([x for x in self.positions().values() if x["ticker"] == "ETH-USDT"]), 1)
        self.assertEqual(len(self.embeds), n_embeds)

        self.fm.range = (p["sl"] * 0.995, p["entry"])
        self.fm.price_shift = 0.97
        cloud_run.main()
        self.assertFalse(any(x["ticker"] == "ETH-USDT" for x in self.positions().values()))
        self.assertTrue(any("Stop Loss Hit" in t for t in self.titles()))
        with open("closed_trades.jsonl", "r", encoding="utf-8") as fh:
            rows = [json.loads(line) for line in fh]
        self.assertEqual(rows[-1]["status"], "SL_HIT")
        self.assertLess(rows[-1]["r"], 0)
        self.assertAlmostEqual(rows[-1]["r"], -1.0, delta=0.15)
        state = engine_state.load_state()
        self.assertIn("ETH-USDT", state["cooldown"])

        self.fm.range, self.fm.price_shift = None, 1.0
        before = len(self.embeds)
        cloud_run.main()
        self.assertFalse(any(x["ticker"] == "ETH-USDT" for x in self.positions().values()))
        self.assertFalse(any("ETH-USDT — BUY" in t for t in self.titles()[before:]))

    def test_sheet_sync_deduplicates_rows_prevents_doubling_and_sends_tp_alerts(self):
        sheet_rows = [
            gs.POSITION_HEADERS,
            ["sig_eth_1", gs.format_pkt(), "watchlist", "ETH-USDT", "BUY", 4000.0, 3900.0, 4100.0, "PENDING", 4200.0, "PENDING", 4300.0, "PENDING", "PENDING", 4000.0, gs.format_pkt()],
            ["sig_eth_2", "2026-10-31 08:06:20 PKT", "watchlist", "ETH-USDT", "BUY", 4010.0, 3910.0, 4110.0, "PENDING", 4210.0, "PENDING", 4310.0, "PENDING", "PENDING", 4010.0, "2026-10-31 08:06:20 PKT"],
            ["sig_eth_3", "2026-10-31 09:06:20 PKT", "watchlist", "ETH-USDT", "BUY", 4020.0, 3920.0, 4120.0, "PENDING", 4220.0, "PENDING", 4320.0, "PENDING", "PENDING", 4020.0, "2026-10-31 09:06:20 PKT"],
        ]
        deleted_batches = []

        def fake_req(method, path, **kw):
            if method == "GET" and "Positions" in path:
                return {"values": sheet_rows}
            if method == "GET" and "fields=sheets" in path:
                return {"sheets": [{"properties": {"sheetId": 2, "title": "Positions"}}]}
            if method == "POST" and path == ":batchUpdate":
                deleted_batches.append(kw["json"]["requests"])
                return {}
            return {}

        with mock.patch.object(gs, "_client", lambda: {"id": "sheet1", "credentials": None}), \
             mock.patch.object(gs, "_request", fake_req):
            reconciled = gs.sync_open_positions({})
            self.assertEqual(list(reconciled.keys()), ["sig_eth_1"])
            self.assertEqual(len(deleted_batches), 1)
            self.assertEqual(len(deleted_batches[0]), 2)

            with open("active_signals.json", "w", encoding="utf-8") as fh:
                json.dump(reconciled, fh)
            self.fm.range = (3990.0, 4220.0)
            with mock.patch.object(cloud_run, "sync_open_positions", lambda p: p), \
                 mock.patch.object(cloud_run, "update_position", lambda e: True):
                cloud_run.main()
            self.assertTrue(any("TP1 Hit" in t for t in self.titles()), self.titles())
            self.assertTrue(any("TP2 Hit" in t for t in self.titles()), self.titles())
            self.assertFalse(any("ETH-USDT — BUY" in t for t in self.titles()), self.titles())
            pos_after = self.positions()["sig_eth_1"]
            self.assertEqual(pos_after["tp1_status"], "HIT")
            self.assertEqual(pos_after["tp2_status"], "HIT")
            self.assertTrue(pos_after.get("sl_at_breakeven"))
            self.assertEqual(pos_after["sl"], 4000.0)

    def test_time_exit_closes_stale_trade(self):
        cloud_run.main()
        positions = self.positions()
        for pos in positions.values():
            pos["created_at_utc"] = (datetime.now(timezone.utc) - timedelta(hours=80)).isoformat()
        with open("active_signals.json", "w", encoding="utf-8") as fh:
            json.dump(positions, fh)
        cloud_run.main()
        self.assertTrue(any("Time exit" in t for t in self.titles()))
        with open("closed_trades.jsonl", "r", encoding="utf-8") as fh:
            self.assertEqual({json.loads(l)["status"] for l in fh}, {"TIME_EXIT"})

    def test_board_and_briefing_are_built_once_per_slot(self):
        wrote = []
        with mock.patch.object(cloud_run, "write_board", lambda top, rows: wrote.append((top, rows)) or True):
            cloud_run.main()
            cloud_run.main()
        self.assertEqual(sum("Market briefing" in t for t in self.titles()), 1)
        top, rows = wrote[0]
        self.assertEqual(len(rows), 3)
        self.assertEqual({r[0] for r in rows}, {"BTC-USDT", "ETH-USDT", "SOL-USDT"})
        self.assertTrue(any("SIGNAL" in r[2] for r in rows))
        self.assertEqual(top[4][0], "Coin")

    def test_etf_signal_scales_and_falls_back_to_btc(self):
        import admiral_core as core
        flows = {"BTC-USDT": {"status": "OK", "seven_day_net_flow_usd_m": 250.0},
                 "ETH-USDT": {"status": "OK", "seven_day_net_flow_usd_m": -400.0},
                 "DOGE-USDT": {"status": "NOT_TRACKED"}}
        with mock.patch.object(etf_flow, "get_institutional_flow", lambda t: flows[t]):
            self.assertAlmostEqual(core.etf_signal("BTC-USDT")[0], 0.5)
            self.assertEqual(core.etf_signal("ETH-USDT")[0], -1.0)
            self.assertAlmostEqual(core.etf_signal("DOGE-USDT")[0], 0.5)

    def test_4h_mode_skips_scan_outside_window(self):
        with mock.patch.dict(os.environ, {"ADMIRAL_SETUP_TF": "4"}):
            self.assertTrue(cloud_run.scan_window_open(datetime(2026, 10, 12, 8, 7, tzinfo=timezone.utc)))
            self.assertTrue(cloud_run.scan_window_open(datetime(2026, 10, 12, 9, 7, tzinfo=timezone.utc)))
            self.assertFalse(cloud_run.scan_window_open(datetime(2026, 10, 12, 10, 7, tzinfo=timezone.utc)))
            self.assertFalse(cloud_run.scan_window_open(datetime(2026, 10, 12, 11, 7, tzinfo=timezone.utc)))

    def test_feed_outage_alert_is_rate_limited(self):
        with mock.patch.object(md, "get_prices", lambda tickers: {}), mock.patch.object(md, "get_price", lambda t: None):
            cloud_run.main()
            cloud_run.main()
        self.assertEqual(sum("Data feed problem" in t for t in self.titles()), 1)


class PortfolioRuleTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc)

    def test_same_direction_and_open_caps(self):
        pos = {"a": {"ticker": "BTC-USDT", "action": "SELL"}, "b": {"ticker": "ETH-USDT", "action": "SELL"}}
        with mock.patch.object(engine_state, "MAX_SAME_DIRECTION", 2):
            ok, why = engine_state.portfolio_gate("SELL", "SOL-USDT", pos, engine_state.load_state("none.json"), self.now)
        self.assertFalse(ok)
        self.assertIn("SELL", why)
        ok, _ = engine_state.portfolio_gate("BUY", "SOL-USDT", pos, engine_state.load_state("none.json"), self.now)
        self.assertTrue(ok)

    def test_circuit_breaker_after_three_losses(self):
        with tempfile.TemporaryDirectory() as d:
            trades = os.path.join(d, "t.jsonl")
            state = engine_state.load_state(os.path.join(d, "none.json"))
            loser = {"ticker": "BTC-USDT", "action": "BUY", "entry": 100.0, "sl": 99.0, "paper_total_pnl_usdt": -1.0}
            for _ in range(3):
                engine_state.record_close(state, dict(loser), "SL_HIT", self.now, path=trades)
            ok, why = engine_state.portfolio_gate("BUY", "XRP-USDT", {}, state, self.now)
            self.assertFalse(ok)
            self.assertIn("circuit breaker", why)
            state2 = engine_state.load_state(os.path.join(d, "none.json"))
            engine_state.record_close(state2, dict(loser), "SL_HIT", self.now, path=trades)
            engine_state.record_close(state2, {**loser, "paper_total_pnl_usdt": 2.0}, "TP3_HIT", self.now, path=trades)
            self.assertEqual(state2["loss_streak"], 0)


if __name__ == "__main__":
    unittest.main()

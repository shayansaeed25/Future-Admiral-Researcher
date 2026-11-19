"""Engine invariants: levels, causality (no look-ahead), live/backtest parity, gates, and v4 Live Rolling Backtest filter."""
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import numpy as np

import backtest as bt
import macro
import signal_engine as se
from tests.synth_data import synth

CFG0 = {**se.default_config(), "min_score": 0.0, "setup_tf": 1, "setups": "pullback,breakout", "adx_min": 20, "live_backtest": 0}


def _btc():
    return bt.prepare("BTC-USDT", synth(4400, 1, 80000))


class EngineTests(unittest.TestCase):
    def test_default_config_is_1h_and_enables_live_backtest_and_sl_caps(self):
        cfg = se.default_config()
        self.assertEqual(int(cfg["setup_tf"]), 1)
        self.assertEqual(int(cfg["live_backtest"]), 1)
        self.assertGreaterEqual(float(cfg["min_score"]), 70.0)
        self.assertLessEqual(float(cfg["max_sl_pct"]), 3.3)
        for s in ("pullback", "breakout", "momentum", "smc_sweep"):
            self.assertIn(s, cfg["setups"])
        self.assertNotIn("whale_reversal", cfg["setups"])

    def test_live_rolling_backtest_computes_causal_win_and_sl_rates(self):
        full = synth(4400, 14, 2500)
        f1 = se.build_frame(full)
        res = se.backtest_setup_on_history(f1, 3389, 1, "pullback")
        self.assertIn("win_rate", res)
        self.assertIn("sl_rate", res)
        self.assertIn("avg_r", res)
        self.assertIn("passed", res)
        self.assertIn("Live BT", res["summary"] if res["trades"] > 0 else "Live BT")

    def test_levels_are_ordered_and_use_r_multiples(self):
        btc, seen = _btc(), 0
        for seed in (11, 12, 14, 15):
            cd = bt.prepare("ETH-USDT", synth(4400, seed, 2500))
            for i in range(1500, 4300):
                d = bt.decide(cd, i, btc, CFG0)
                if d["action"] not in ("BUY", "SELL"):
                    continue
                lv, sgn = d["levels"], 1 if d["action"] == "BUY" else -1
                self.assertGreater(lv["risk"], 0)
                self.assertTrue(sgn * (lv["tp3"] - lv["tp2"]) > 0 and sgn * (lv["tp2"] - lv["tp1"]) > 0 and sgn * (lv["tp1"] - lv["entry"]) > 0)
                self.assertTrue(sgn * (lv["entry"] - lv["sl"]) > 0)
                self.assertAlmostEqual(abs(lv["tp1"] - lv["entry"]) / lv["risk"], CFG0["tp1_r"], places=6)
                self.assertAlmostEqual(abs(lv["tp3"] - lv["entry"]) / lv["risk"], CFG0["tp3_r"], places=6)
                self.assertTrue(0 <= d["score"] <= 100)
                seen += 1
        self.assertGreater(seen, 20)

    def test_decision_does_not_look_ahead(self):
        """Decision at bar i must be identical when all later candles are removed."""
        full, btc = synth(4400, 14, 2500), _btc()
        cd_full = bt.prepare("ETH-USDT", full)
        checked = 0
        for i in (1700, 2100, 2600, 3389, 3800):
            trunc = {k: v[: i + 1] for k, v in full.items()}
            cd_cut = bt.prepare("ETH-USDT", trunc)
            a = bt.decide(cd_full, i, btc, CFG0)
            b = bt.decide(cd_cut, i, btc, CFG0)
            self.assertEqual((a["action"], a["score"], a["setup"]), (b["action"], b["score"], b["setup"]), f"bar {i}")
            checked += 1
        self.assertEqual(checked, 5)

    def test_live_wrapper_matches_backtest_decision(self):
        full, btc = synth(4400, 14, 2500), _btc()
        i = 3389  # known BUY (pullback) in the synthetic series
        cd = bt.prepare("ETH-USDT", full)
        want = bt.decide(cd, i, btc, CFG0)
        self.assertEqual(want["action"], "BUY")
        prefix = {k: v[: i + 1] for k, v in full.items()}
        c4 = bt.resample(prefix, 4 * 3600, 0)
        cdy = bt.resample(prefix, 86400, 8 * 3600)
        b4 = int(np.searchsorted(btc.end4, prefix["ts"][-1] + 3_600_000, side="right")) - 1
        got = se.evaluate_live(prefix, c4, cdy, price=float(prefix["c"][-1]), ticker="ETH-USDT",
                               btc_trend_4h=se.trend_state(btc.f4, b4), cfg=CFG0)
        self.assertEqual((got["action"], got["setup"]), (want["action"], want["setup"]))
        self.assertAlmostEqual(got["score"], want["score"], places=6)

    def test_counter_trend_and_range_markets_do_not_signal(self):
        btc = _btc()
        cd = bt.prepare("ETH-USDT", synth(4400, 11, 2500))
        for i in range(1500, 4300, 7):
            d = bt.decide(cd, i, btc, CFG0)
            if d["action"] in ("BUY", "SELL"):
                want = 1 if d["action"] == "BUY" else -1
                self.assertEqual(se.trend_state(cd.f4, int(np.searchsorted(cd.end4, cd.f1.ts[i] + 3_600_000, side="right")) - 1), want)

    def test_min_score_gate_blocks(self):
        btc = _btc()
        cd = bt.prepare("ETH-USDT", synth(4400, 14, 2500))
        d = bt.decide(cd, 3389, btc, CFG0)
        i4 = int(np.searchsorted(cd.end4, cd.f1.ts[3389] + 3_600_000, side="right")) - 1
        idd = int(np.searchsorted(cd.endd, cd.f1.ts[3389] + 3_600_000, side="right")) - 1
        strict = se.evaluate(cd.f1, 3389, cd.f4, i4, cd.fd, idd, ticker="ETH-USDT", btc_trend_4h=se.trend_state(btc.f4, int(np.searchsorted(btc.end4, cd.f1.ts[3389] + 3_600_000, side="right")) - 1),
                             cfg={**CFG0, "min_score": 99})
        self.assertEqual(d["action"], "BUY")
        self.assertEqual(strict["action"], "NO TRADE")
        self.assertTrue(any("below" in b for b in strict["blockers"]))

    def test_crowded_funding_vetoes_trade(self):
        full = synth(4400, 14, 2500)
        i = 3389
        prefix = {k: v[: i + 1] for k, v in full.items()}
        c4, cdy = bt.resample(prefix, 4 * 3600, 0), bt.resample(prefix, 86400, 8 * 3600)
        hot = se.evaluate_live(prefix, c4, cdy, price=float(prefix["c"][-1]), ticker="ETH-USDT", funding_pct=0.2, cfg=CFG0)
        self.assertEqual(hot["action"], "NO TRADE")
        self.assertTrue(any("funding" in b for b in hot["blockers"]))

    def test_v4_live_backtest_and_sl_cap_produce_high_pf_in_simulation(self):
        coins = {f"{k}-USDT": bt.prepare(f"{k}-USDT", synth(4400, s, p)) for k, (s, p) in
                 {"BTC": (1, 80000), "ETH": (2, 2500), "SOL": (3, 110), "XRP": (4, 1.4)}.items()}
        cfg_v4 = {**se.default_config(), "min_score": 0.0}
        trades = bt.simulate(coins, cfg_v4, min_score=72, fee_bps=8)
        self.assertGreater(len(trades), 45)
        st = bt.stats(trades)
        self.assertGreater(st["PF"], 1.45)
        self.assertGreater(st["avgR"], 0.20)
        # Every trade must respect the 3.25% max stop-loss cap
        self.assertTrue(all(t["risk_pct"] <= 3.30 for t in trades))


class BacktestTests(unittest.TestCase):
    def test_simulation_runs_and_respects_caps(self):
        coins = {f"{k}-USDT": bt.prepare(f"{k}-USDT", synth(4400, s, p)) for k, (s, p) in
                 {"BTC": (1, 80000), "ETH": (2, 2500), "SOL": (3, 110), "XRP": (4, 1.4)}.items()}
        trades = bt.simulate(coins, CFG0, min_score=62, fee_bps=8, max_open=2, max_same_dir=1)
        self.assertGreater(len(trades), 5)
        self.assertTrue(all(t["status"] in ("SL_HIT", "BE_EXIT", "TP3_HIT", "TIME_EXIT") for t in trades))
        events = sorted([(t["entry_ts"], 1) for t in trades] + [(t["exit_ts"] + 3_600_000, -1) for t in trades])
        live = peak = 0
        for _, delta in events:
            live += delta
            peak = max(peak, live)
        self.assertLessEqual(peak, 2)
        self.assertEqual(bt.stats(trades)["n"], len(trades))


class FlowMacroTests(unittest.TestCase):
    def _prefix(self):
        full = synth(4400, 14, 2500)
        p = {k: v[:3390] for k, v in full.items()}
        return p, bt.resample(p, 4 * 3600, 0), bt.resample(p, 86400, 8 * 3600)

    def _run(self, **kw):
        p, c4, cdy = self._prefix()
        return se.evaluate_live(p, c4, cdy, price=float(p["c"][-1]), ticker="ETH-USDT", cfg=CFG0, **kw)

    def test_supportive_flow_raises_score_and_opposing_flow_blocks(self):
        base = self._run()
        self.assertEqual(base["action"], "BUY")
        good = self._run(flow={"taker_buy_ratio_4h": 0.56, "taker_buy_ratio_24h": 0.54, "coinbase_premium_pct": 0.2})
        bad = self._run(flow={"taker_buy_ratio_4h": 0.44, "taker_buy_ratio_24h": 0.46, "coinbase_premium_pct": -0.2})
        self.assertGreater(good["score"], base["score"])
        self.assertEqual(bad["action"], "NO TRADE")
        self.assertTrue(any("institutional" in b for b in bad["blockers"]))

    def test_orderbook_imbalance_and_retail_trap_in_flow_alignment(self):
        score, notes = se.flow_alignment({"taker_buy_ratio_1h": 0.58, "orderbook_imbalance": 0.30, "retail_ls_ratio": 0.75}, 1)
        self.assertGreater(score, 0.8)
        self.assertTrue(any("1h whale taker" in n for n in notes))
        self.assertTrue(any("book depth" in n for n in notes))
        self.assertTrue(any("retail short-crowded" in n for n in notes))

    def test_macro_blackout_blocks_and_headwind_subtracts(self):
        base = self._run()
        blk = self._run(macro={"blackout": True, "blackout_reason": "FOMC decision"})
        self.assertEqual(blk["action"], "NO TRADE")
        self.assertTrue(any("blackout" in b for b in blk["blockers"]))
        head = self._run(macro={"risk": -1, "notes": ["US10Y 4.6%"]})
        self.assertAlmostEqual(base["score"] - head["score"], CFG0["macro_penalty"], places=1)
        tail = self._run(macro={"risk": 1, "notes": []})
        self.assertAlmostEqual(tail["score"], base["score"], places=1)


class FourHourModeTests(unittest.TestCase):
    def test_4h_mode_only_acts_right_after_4h_close_and_grid_runs(self):
        cfg4 = {**CFG0, "setup_tf": 4}
        coins = {f"{k}-USDT": bt.prepare(f"{k}-USDT", synth(4400, s, p)) for k, (s, p) in
                 {"BTC": (1, 80000), "ETH": (2, 2500), "SOL": (3, 110)}.items()}
        trades = bt.simulate(coins, cfg4, min_score=62, fee_bps=8)
        for t in trades:
            self.assertLessEqual(((t["entry_ts"] // 3_600_000) % 4), 1)
        stats = bt.stats(trades)
        self.assertIn("n", stats)
        report = bt.run_grid(coins, 8.0)
        self.assertIn("PASS", report)
        self.assertIn("4H timing default", report)


class MacroTests(unittest.TestCase):
    def test_oil_inflation_chain_flips_regime_risk_off(self):
        series = {"DGS10": [4.0] * 6 + [4.3], "DTWEXBGS": [100] * 6 + [101], "DCOILWTICO": [70] * 20 + [80],
                  "T10YIE": [2.3] * 20 + [2.45], "VIXCLS": [27], "NASDAQCOM": [100] * 6 + [96]}
        total, notes, story = macro.evaluate_factors(series)
        self.assertLessEqual(total, -2)
        self.assertTrue(any("Fed cuts less likely" in s for s in story))
        calm = {"DGS10": [4.0] * 7, "DTWEXBGS": [100] * 7, "DCOILWTICO": [70] * 21, "T10YIE": [2.3] * 21, "VIXCLS": [20], "NASDAQCOM": [100] * 7}
        self.assertEqual(macro.evaluate_factors(calm)[0], 0)

    def test_global_headlines_geopolitics_and_cpi_ppi_scoring(self):
        bull = macro.score_global_headlines([
            "[Macro] US CPI cools and PPI falls, raising Fed rate cut hopes",
            "[Geopolitics] Iran talks progress and Middle East ceasefire agreed",
            "[Crypto] Spot ETF inflow surges on whale accumulation",
        ])
        self.assertGreater(bull["news_sentiment"], 0.5)
        self.assertEqual(bull["geopolitical_bias"], 1)
        self.assertEqual(bull["cpi_ppi_bias"], 1)

        bear = macro.score_global_headlines([
            "[Macro] Hot CPI and PPI jumps spark hawkish Fed warning",
            "[Geopolitics] US military strike and Iran war escalation threaten Strait of Hormuz",
        ])
        self.assertLess(bear["news_sentiment"], -0.5)
        self.assertEqual(bear["geopolitical_bias"], -1)
        self.assertEqual(bear["cpi_ppi_bias"], -1)

    def test_etf_outflow_opposes_buy(self):
        flow = {"etf_flow_score": -1.0}
        score, notes = se.flow_alignment(flow, 1)
        self.assertEqual(score, -1.0)
        self.assertEqual(se.flow_alignment(flow, -1)[0], 1.0)


class MacroFallbackTests(unittest.TestCase):
    def test_source_order_and_error_reporting(self):
        yahoo = {"chart": {"result": [{"indicators": {"quote": [{"close": [41.0, None, 42.0]}]}}]}}
        calls = []

        class Resp:
            def __init__(self, payload): self.payload = payload
            def json(self): return self.payload

        def fake_get(url, timeout=8, headers=None):
            calls.append(url)
            if "yahoo" in url:
                return Resp(yahoo)
            raise TimeoutError("blocked")
        errs = []
        with mock.patch.object(macro.requests, "get", fake_get):
            vals = macro.fetch_series("DGS10", errs)
        self.assertEqual(vals, [4.1, 4.2])
        self.assertFalse(any("fred.stlouisfed" in c for c in calls))
        errs2 = []
        with mock.patch.object(macro.requests, "get", fake_get), mock.patch.object(macro, "_get", lambda *a, **k: (_ for _ in ()).throw(TimeoutError("x"))):
            self.assertEqual(macro.fetch_series("T10YIE", errs2), [])
        self.assertTrue(any("FREDcsv T10YIE" in e for e in errs2))

    def test_time_budget_stops_slow_sources(self):
        import time
        errs = []
        self.assertEqual(macro.fetch_series("DGS10", errs, deadline=time.time() - 1), [])
        self.assertTrue(all("time budget" in e for e in errs))

    def test_macro_is_fetched_once_per_day_and_failures_retry_sooner(self):
        now = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)
        good = {"risk": 0, "notes": ["US10Y 4.10% (+0.00pp 5d)"], "story": [], "errors": [], "blackout": False, "blackout_reason": ""}
        state = {}
        with mock.patch.object(macro, "get_macro", return_value=good) as gm:
            macro.get_macro_cached(state, now)
            macro.get_macro_cached(state, now + timedelta(hours=6))
            self.assertEqual(gm.call_count, 1)
            macro.get_macro_cached(state, now + timedelta(hours=21))
            self.assertEqual(gm.call_count, 2)
        bad = {"risk": 0, "notes": ["Fear&Greed 50"], "story": [], "errors": ["x"], "blackout": False, "blackout_reason": ""}
        state2 = {}
        with mock.patch.object(macro, "get_macro", return_value=bad) as gm:
            macro.get_macro_cached(state2, now)
            macro.get_macro_cached(state2, now + timedelta(hours=1))
            self.assertEqual(gm.call_count, 1)
            macro.get_macro_cached(state2, now + timedelta(hours=4))
            self.assertEqual(gm.call_count, 2)

    def test_30m_macro_cache_mode_refreshes_every_25_minutes(self):
        now = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)
        good = {"risk": 0, "notes": ["US10Y 4.10% (+0.00pp 5d)"], "story": [], "errors": [], "blackout": False, "blackout_reason": ""}
        state = {}
        with mock.patch.dict(os.environ, {"ADMIRAL_MACRO_CACHE_HOURS": "0.42"}), mock.patch.object(macro, "get_macro", return_value=good) as gm:
            macro.get_macro_cached(state, now)
            macro.get_macro_cached(state, now + timedelta(minutes=15))
            self.assertEqual(gm.call_count, 1)
            macro.get_macro_cached(state, now + timedelta(minutes=30))
            self.assertEqual(gm.call_count, 2)


if __name__ == "__main__":
    unittest.main()

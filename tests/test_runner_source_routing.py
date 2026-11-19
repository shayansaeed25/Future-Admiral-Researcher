"""Regression tests for the configured crypto sources and report batching."""

import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

import admiral_core
from report_publisher import publish_report
from tradingagents.dataflows import crypto_orderflow, y_finance
from tradingagents.dataflows import smc


ROOT = Path(__file__).resolve().parents[1]


class RunnerSourceRoutingTests(unittest.TestCase):
    def test_watchlist_is_the_requested_six_usdt_pairs(self):
        payload = json.loads((ROOT / "watchlist.json").read_text(encoding="utf-8"))
        self.assertEqual(
            [coin["ticker"] for coin in payload["coins"]],
            ["BTC-USDT", "ETH-USDT", "SOL-USDT", "XRP-USDT", "DOGE-USDT", "SUI-USDT"],
        )
        self.assertTrue(all(coin["interval_hours"] == 1 for coin in payload["coins"]))

    def test_selected_coins_have_coingecko_price_ids(self):
        for base, coin_id in {
            "BTC": "bitcoin", "SOL": "solana", "XRP": "ripple",
            "LINK": "chainlink", "AAVE": "aave", "NEAR": "near",
        }.items():
            with self.subTest(base=base):
                self.assertEqual(admiral_core._COINGECKO_IDS[base], coin_id)

    def test_current_price_uses_coingecko_before_exchange_fallback(self):
        with patch.object(admiral_core, "_get_with_retry", return_value={"aave": {"usd": 100.0}}) as get:
            with patch.object(admiral_core.requests, "get") as okx_get:
                price, error = admiral_core.get_current_price("AAVE-USDT")
        self.assertEqual((price, error), (100.0, None))
        self.assertIn("api.coingecko.com", get.call_args.args[0])
        okx_get.assert_not_called()

    def test_historical_crypto_fallback_excludes_binance_and_yahoo(self):
        with patch.object(y_finance, "get_okx_crypto_data", return_value=None) as okx:
            with patch.object(y_finance, "get_coingecko_crypto_data", return_value=None) as gecko:
                with patch.object(y_finance.yf, "Ticker") as yahoo:
                    with self.assertRaises(y_finance.NoMarketDataError):
                        y_finance.get_YFin_data_online("AAVE-USDT", "2026-01-01", "2026-02-01")
        okx.assert_called_once()
        gecko.assert_called_once()
        yahoo.assert_not_called()

    def test_orderflow_routes_only_to_okx_helper(self):
        def fill_okx(data, symbol, errors):
            data["funding_rate"] = "0.01%"
            data["fallbacks"].append("OKX test data")

        crypto_orderflow.get_realtime_orderflow.cache_clear()
        try:
            with patch.object(crypto_orderflow, "_fill_okx_fallbacks", side_effect=fill_okx) as okx:
                first = crypto_orderflow.get_realtime_orderflow("AAVE-USDT")
                second = crypto_orderflow.get_realtime_orderflow("AAVE-USDT")
            okx.assert_called_once()
            self.assertIs(first, second)
            self.assertEqual(first["funding_rate"], "0.01%")
        finally:
            crypto_orderflow.get_realtime_orderflow.cache_clear()

    def test_multi_timeframe_swing_data_is_fetched_once_per_run(self):
        rows = [[index, 100 + index, 102 + index, 99 + index, 101 + index, 50] for index in range(40)]
        smc.analyze_smc_timeframes.cache_clear()
        try:
            with patch.object(smc, "_candles", return_value=rows) as candles:
                first = smc.analyze_smc_timeframes("CACHE-TEST-USDT")
                second = smc.analyze_smc_timeframes("CACHE-TEST-USDT")
            self.assertIs(first, second)
            self.assertEqual(candles.call_count, 3)
        finally:
            smc.analyze_smc_timeframes.cache_clear()

    def test_quota_failure_rotates_through_all_configured_provider_keys(self):
        keys = [f"test-key-{index}" for index in range(9)]
        quota_error = RuntimeError("429 RESOURCE_EXHAUSTED")
        with patch.object(admiral_core, "MAX_KEYS_PER_PROVIDER", 9):
            with patch.object(admiral_core, "TradingAgentsGraph", side_effect=quota_error) as graph:
                with patch.dict(os.environ, {}, clear=False):
                    with self.assertRaisesRegex(Exception, r"9/9 key\(s\) tried"):
                        admiral_core._run_provider_pool(
                            "google", keys, "GOOGLE_API_KEY", "fast-model", "deep-model",
                            "AAVE-USDT", "2026-09-26", 0,
                        )
        self.assertEqual(graph.call_count, 9)

    def test_report_publish_queues_without_per_coin_git_push(self):
        report = ROOT / "reports" / "BTC-USDT" / "complete_report.md"
        self.assertTrue(report.parent.is_dir())
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}):
            self.assertTrue(publish_report(report))


if __name__ == "__main__":
    unittest.main()

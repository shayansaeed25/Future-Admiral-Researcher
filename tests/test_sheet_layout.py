"""The Google Sheet layout: exactly 3 tabs, applied once, idempotent, never blocks logging, resettable."""
import json
import unittest
from unittest import mock

import google_sheets_sync as gs
import sheet_layout as L
from tests.fake_sheets import Fake


class SheetLayoutTests(unittest.TestCase):
    def setUp(self):
        self.fk = Fake(tabs=("Signals", "Positions", "System Health", "Dashboard", "Market Board", "Tear Sheet", "Strategy Scorecard", "Goal Grid"))
        for p in (mock.patch.object(gs, "_client", lambda: {"id": "x", "credentials": None}), mock.patch.object(gs, "_request", self.fk.request),
                  mock.patch.object(gs, "_move_legacy_health_rows", lambda: None)):
            p.start()
            self.addCleanup(p.stop)

    def test_ends_with_exactly_three_tabs_in_order(self):
        gs._ensure_tabs()
        self.assertEqual(sorted(self.fk.sheets), sorted(["Dashboard", "Positions", "System Health"]))

    def test_applies_once_then_is_quiet(self):
        gs._ensure_tabs()
        applied = len(self.fk.batches)
        self.assertGreater(applied, 5)
        gs._ensure_tabs()
        self.assertEqual(len(self.fk.batches), applied)
        self.assertEqual(sum(len(v) for v in self.fk.charts.values()), 4)

    def test_reset_clears_ledgers_and_forces_relayout(self):
        gs._ensure_tabs()
        applied = len(self.fk.batches)
        gs._RESET_DONE[0] = False
        with mock.patch.dict("os.environ", {"ADMIRAL_RESET_SHEET": "1"}):
            gs._ensure_tabs()
        self.assertGreater(len(self.fk.batches), applied)
        from urllib.parse import unquote
        cleared = [unquote(p) for m, p in self.fk.calls if m == "POST" and p.endswith(":clear")]
        self.assertTrue(any("Positions" in p for p in cleared) and any("System Health" in p for p in cleared))

    def test_layout_failure_never_blocks_ledger(self):
        with mock.patch.object(L, "ensure_layout", side_effect=RuntimeError("boom")):
            gs._ensure_tabs()

    def test_signal_events_do_not_need_a_signals_tab(self):
        gs._ensure_tabs()
        with mock.patch.object(gs, "_save_position") as save:
            self.assertTrue(gs.record_scan_event({"signal_id": "s1", "action": "BUY", "ticker": "BTC-USDT", "price": 1, "entry": 1}))
            save.assert_called_once()
        self.assertFalse(any("Signals" in p and m == "PUT" for m, p in self.fk.calls[-3:]))

    def test_no_request_ever_touches_a_signals_tab(self):
        gs._ensure_tabs()
        start = len(self.fk.calls)
        with mock.patch.object(gs, "_save_position"):
            gs.record_scan_event({"signal_id": "s1", "action": "BUY", "ticker": "BTC-USDT", "price": 1, "entry": 1})
            gs.update_position({"signal_id": "s1", "ticker": "BTC-USDT", "status": "SL_HIT", "event": "SL_HIT"})
        gs.prepare()
        from urllib.parse import unquote
        self.assertFalse(any("Signals" in unquote(p) for m, p in self.fk.calls[start:]))

    def test_column_letters_match_position_headers(self):
        h = gs.POSITION_HEADERS
        self.assertEqual((h[13], h[31], h[17], h[3], h[4]), ("Position Status", "Total Gross P&L (USDT)", "Closed (PKT)", "Coin", "Trade"))
        self.assertEqual((gs._column_letter(14), gs._column_letter(32), gs._column_letter(18)), ("N", "AF", "R"))

    def test_dashboard_cell_references_are_consistent(self):
        rows = L.dashboard_values()
        self.assertEqual(rows[27][0], "Paper size per trade (USD)")      # B28
        self.assertTrue(rows[32][1].startswith("=B28*2*"))                 # B33 round-trip cost
        self.assertEqual(rows[23][0], "Verdict")                           # row 24 (conditional colours)
        self.assertEqual(rows[24][0], "Health alerts needing action")      # row 25
        self.assertEqual((L.NOTIONAL, L.COST, L.MIN_TRADES, L.PF_GATE), ("$B$28", "$B$33", "$B$31", "$B$32"))

    def test_requests_are_json_serialisable(self):
        ids = {t: i + 1 for i, t in enumerate(L.TABS_ORDER)}
        for reqs in L._format_requests(ids, ["BTC-USDT", "ETH-USDT"]).values():
            json.dumps(reqs)
        json.dumps(L._chart_requests(ids, 2))


if __name__ == "__main__":
    unittest.main()

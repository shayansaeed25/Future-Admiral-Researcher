import unittest
from unittest.mock import patch

from paper_trading import apply_paper_outcome, paper_signal_fields
from position_state import evaluate_position_range
import google_sheets_sync as sheets


def make_position(action="BUY"):
    entry = 200.0
    levels = {"sl": 190.0, "tp1": 210.0, "tp2": 220.0, "tp3": 230.0}
    if action == "SELL":
        levels = {"sl": 210.0, "tp1": 190.0, "tp2": 180.0, "tp3": 170.0}
    return {
        "entry": entry, "action": action, **levels,
        "tp1_status": "PENDING", "tp2_status": "PENDING", "tp3_status": "PENDING",
        **paper_signal_fields(entry, action, 100.0),
    }


class PaperTradingTests(unittest.TestCase):
    def test_fixed_100_notional_calculates_units(self):
        fields = paper_signal_fields(200.0, "BUY", 100.0)
        self.assertEqual(fields["paper_notional_usdt"], 100.0)
        self.assertEqual(fields["paper_initial_qty"], 0.5)
        self.assertEqual(fields["paper_total_pnl_usdt"], 0.0)

    def test_tp1_then_stop_realizes_partial_profit_and_remaining_loss(self):
        position = make_position()
        target = evaluate_position_range(position, 201.0, 211.0)
        apply_paper_outcome(position, target, 211.0)
        self.assertEqual(position["tp1_status"], "HIT")
        self.assertAlmostEqual(position["paper_realized_pnl_usdt"], 100.0 / 3.0 * 0.05)

        stopped = evaluate_position_range(position, 189.0, 195.0)
        apply_paper_outcome(position, stopped, 189.0)
        # After TP1 the stop sits at entry (breakeven), so the remainder exits flat.
        self.assertEqual(stopped["status"], "BE_EXIT")
        self.assertEqual(position["paper_remaining_qty"], 0.0)
        self.assertAlmostEqual(position["paper_total_pnl_usdt"], 100.0 / 3.0 * 0.05)

    def test_short_tp3_closes_all_paper_quantity_with_gross_profit(self):
        position = make_position("SELL")
        outcome = evaluate_position_range(position, 169.0, 199.0)
        apply_paper_outcome(position, outcome, 170.0)
        self.assertEqual(outcome["status"], "TP3_HIT")
        self.assertEqual(position["paper_remaining_qty"], 0.0)
        self.assertAlmostEqual(position["paper_total_pnl_usdt"], 10.0)

    def test_ambiguous_stop_and_target_uses_stop_first(self):
        position = make_position()
        outcome = evaluate_position_range(position, 189.0, 211.0)
        apply_paper_outcome(position, outcome, 205.0)
        self.assertTrue(outcome["ambiguous"])
        self.assertEqual(position["tp1_status"], "PENDING")
        self.assertEqual(position["paper_total_pnl_usdt"], -5.0)

    def test_sheet_schemas_and_position_row_match_new_paper_columns(self):
        self.assertEqual(len(sheets.SIGNAL_HEADERS), 40)
        self.assertEqual(len(sheets.POSITION_HEADERS), 34)
        self.assertEqual(sheets._column_letter(27), "AA")
        self.assertEqual(sheets._column_letter(34), "AH")

        appended = []
        position = {
            "signal_id": "paper-test", "entry": 200, "action": "BUY", "sl": 190,
            "tp1": 210, "tp2": 220, "tp3": 230,
            "paper_notional_usdt": 100, "paper_initial_qty": 0.5,
            "paper_remaining_qty": 0.5, "paper_realized_pnl_usdt": 0,
            "paper_unrealized_pnl_usdt": 0, "paper_total_pnl_usdt": 0,
            "paper_return_pct": 0, "paper_pnl_method": "paper-only",
        }
        with patch.object(sheets, "_position_rows", return_value=[sheets.POSITION_HEADERS]), \
             patch.object(sheets, "_append", side_effect=lambda tab, values: appended.append((tab, values))):
            sheets._save_position(position)
        self.assertEqual(len(appended), 1)
        tab, row = appended[0]
        self.assertEqual(tab, "Positions")
        self.assertEqual(len(row), len(sheets.POSITION_HEADERS))
        self.assertEqual(row[26], 100)
        self.assertEqual(row[31], 0)
        self.assertEqual(row[33], "paper-only")

    def test_signal_event_writes_all_new_signal_columns(self):
        rows = []
        event = {
            "run_id": "run-1", "signal_id": "signal-1", "ticker": "BTC-USDT",
            "event": "SIGNAL", "action": "BUY", "price": 200.0,
            "paper_notional_usdt": 100.0, "paper_initial_qty": 0.5,
            "paper_total_pnl_usdt": 0.0, "paper_return_pct": 0.0,
        }
        with patch.object(sheets, "_client", return_value={"id": "mock"}), \
             patch.object(sheets, "_ensure_tabs"), \
             patch.object(sheets, "_upsert_scan_row", side_effect=rows.append), \
             patch.object(sheets, "_save_position") as save:
            self.assertTrue(sheets.record_scan_event(event))
        self.assertEqual(rows, [])  # there is no separate Signals tab any more: the signal is one row in Positions
        save.assert_called_once()
        self.assertEqual(save.call_args[0][0]["signal_id"], "signal-1")
        self.assertEqual(save.call_args[0][0]["paper_notional_usdt"], 100.0)


if __name__ == "__main__":
    unittest.main()

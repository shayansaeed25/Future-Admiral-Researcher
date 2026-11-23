import json
import os
import tempfile
import unittest
from datetime import datetime, timezone

import numpy as np

import audit_journal
import engine_state
import signal_engine as se
from tests.synth_data import synth


class V8GuardsAndJournalTests(unittest.TestCase):
    def test_btc_1h_relief_bounce_and_extreme_fear_parameters_work(self):
        c1, c4, cd = synth(seed=7)
        f1, f4, fd = se.build_frame(c1), se.build_frame(c4), se.build_frame(cd)
        i1 = len(f1) - 1
        i4 = len(f4) - 1
        idd = len(fd) - 1
        res = se.evaluate(
            f1, i1, f4, i4, fd, idd,
            ticker="ETH-USDT", btc_trend_4h=-1, btc_trend_1h=1,
            macro={"risk": 0, "fear_greed": 11},
        )
        self.assertIn("action", res)
        self.assertIn("blockers", res)

    def test_circuit_breaker_defaults_are_2_losses_6_hours(self):
        self.assertEqual(engine_state.PAUSE_AFTER_LOSSES, 2)
        self.assertEqual(engine_state.PAUSE_HOURS, 6.0)
        state = {"cooldown": {}, "paused_until": "", "loss_streak": 0}
        now = datetime(2026, 11, 23, 12, 0, tzinfo=timezone.utc)
        pos1 = {"signal_id": "s1", "ticker": "LTC-USDT", "action": "SELL", "entry": 40.0, "sl": 41.0, "paper_total_pnl_usdt": -2.5}
        pos2 = {"signal_id": "s2", "ticker": "DOT-USDT", "action": "SELL", "entry": 0.65, "sl": 0.67, "paper_total_pnl_usdt": -2.4}
        with tempfile.TemporaryDirectory() as td:
            tf = os.path.join(td, "closed.jsonl")
            engine_state.record_close(state, pos1, "SL_HIT", now, path=tf)
            self.assertIsNone(engine_state.paused_until(state, now))
            engine_state.record_close(state, pos2, "SL_HIT", now, path=tf)
            self.assertIsNotNone(engine_state.paused_until(state, now))

    def test_audit_journal_rotates_and_records_events(self):
        with tempfile.TemporaryDirectory() as td:
            jp = os.path.join(td, "audit_journal.jsonl")
            for k in range(10):
                audit_journal.append_event({"type": "TEST", "idx": k}, path=jp, max_entries=5)
            lines = [json.loads(ln) for ln in open(jp, encoding="utf-8") if ln.strip()]
            self.assertEqual(len(lines), 5)
            self.assertEqual(lines[0]["idx"], 5)
            self.assertEqual(lines[-1]["idx"], 9)


if __name__ == "__main__":
    unittest.main()

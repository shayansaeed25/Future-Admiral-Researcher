"""Lightweight rotating audit journal saved inside the repository (audit_journal.jsonl).

Records:
- SCAN_SUMMARY: BTC 1H/4H trend & RSI, Fear & Greed, open/closed/new counts, top blocker reasons.
- SIGNAL_OPENED: Full regime, backtest, BTC context, and last 5 closed 1H OHLCV candles.
- TRADE_CLOSED: Outcome (SL_HIT/TP3_HIT/BE_EXIT/TIME_EXIT), P&L, hold hours, and BTC price at close.

Auto-rotates to keep the latest MAX_ENTRIES lines so the repo ZIP stays fast and compact.
"""
from __future__ import annotations

import json
import os
from collections import Counter
from datetime import datetime, timezone
from typing import Any

JOURNAL_FILE = "audit_journal.jsonl"
MAX_ENTRIES = int(os.getenv("ADMIRAL_AUDIT_MAX_ENTRIES", "400") or "400")


def _utc_now(now: datetime | None = None) -> datetime:
    return now or datetime.now(timezone.utc)


def append_event(event: dict[str, Any], path: str = JOURNAL_FILE, max_entries: int = MAX_ENTRIES) -> None:
    try:
        line = json.dumps(event, separators=(",", ":"), default=str)
        lines: list[str] = []
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as fh:
                lines = [ln.rstrip("\n") for ln in fh if ln.strip()]
        lines.append(line)
        if len(lines) > max_entries:
            lines = lines[-max_entries:]
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    except OSError:
        pass


def candle_tail(c1: dict | None, count: int = 5) -> list[dict[str, Any]]:
    """Extract the last `count` closed 1H OHLCV candles for forensic post-mortems."""
    if not c1 or c1.get("c") is None or len(c1["c"]) == 0:
        return []
    n = len(c1["c"])
    start = max(0, n - count)
    out = []
    for i in range(start, n):
        out.append({
            "ts": int(c1["ts"][i]) if c1.get("ts") is not None else 0,
            "o": round(float(c1["o"][i]), 6),
            "h": round(float(c1["h"][i]), 6),
            "l": round(float(c1["l"][i]), 6),
            "c": round(float(c1["c"][i]), 6),
            "v": round(float(c1["v"][i]), 2),
        })
    return out


def record_signal_opened(result: dict[str, Any], signal_id: str, run_id: str,
                         ctx: dict[str, Any] | None = None, now: datetime | None = None,
                         path: str = JOURNAL_FILE) -> None:
    ts = _utc_now(now)
    macro = (ctx or {}).get("macro") or result.get("macro") or {}
    lv = result.get("levels") or {}
    regime = result.get("market_regime") or {}
    event = {
        "type": "SIGNAL_OPENED",
        "ts_utc": ts.isoformat(),
        "run_id": run_id,
        "signal_id": signal_id,
        "ticker": result.get("ticker"),
        "action": result.get("action"),
        "setup": result.get("setup"),
        "grade": result.get("grade"),
        "score": round(float(result.get("quality") or 0.0), 1),
        "entry": result.get("cmp"),
        "sl": lv.get("sl"),
        "sl_pct": round(float(lv.get("risk_pct") or 0.0), 2),
        "tp1": lv.get("tp1"),
        "tp3": lv.get("tp3"),
        "trends": regime.get("timeframe_trends"),
        "rsi_1h": regime.get("rsi_1h"),
        "adx_4h": regime.get("adx_4h"),
        "cmf_4h": regime.get("cmf_4h"),
        "live_backtest": result.get("live_backtest"),
        "btc_ctx": (ctx or {}).get("btc_snapshot") or {"btc_4h": (ctx or {}).get("btc_4h"), "btc_1h": (ctx or {}).get("btc_1h")},
        "fear_greed": macro.get("fear_greed"),
        "macro_risk": macro.get("risk"),
        "candles_1h_tail": result.get("candles_1h_tail") or [],
    }
    append_event(event, path=path)


def record_trade_closed(position: dict[str, Any], status: str, run_id: str,
                        btc_price: float | None = None, hold_hours: float | None = None,
                        now: datetime | None = None, path: str = JOURNAL_FILE) -> None:
    ts = _utc_now(now)
    event = {
        "type": "TRADE_CLOSED",
        "ts_utc": ts.isoformat(),
        "run_id": run_id,
        "signal_id": position.get("signal_id"),
        "ticker": position.get("ticker"),
        "action": position.get("action"),
        "setup": position.get("setup", ""),
        "status": status,
        "entry": position.get("entry"),
        "sl": position.get("original_sl") or position.get("sl"),
        "exit_price": position.get("exit_price"),
        "pnl_usdt": round(float(position.get("paper_total_pnl_usdt", 0.0) or 0.0), 4),
        "hold_hours": round(float(hold_hours), 2) if hold_hours is not None else None,
        "opened_utc": position.get("created_at_utc"),
        "btc_price_at_close": btc_price,
    }
    append_event(event, path=path)


def record_scan_summary(run_id: str, entries: list[dict[str, Any]], ctx: dict[str, Any] | None,
                        open_count: int, closed_count: int, new_signals: list[tuple[str, str]],
                        paused: bool = False, now: datetime | None = None,
                        path: str = JOURNAL_FILE) -> None:
    ts = _utc_now(now)
    macro = (ctx or {}).get("macro") or {}
    reasons: Counter[str] = Counter()
    for e in entries:
        st = str(e.get("status") or "")
        if "WAIT" in st or "GATED" in st:
            why = str(e.get("why") or "other").split("—")[0].split("(")[0].strip()[:48]
            if why:
                reasons[why] += 1
    event = {
        "type": "SCAN_SUMMARY",
        "ts_utc": ts.isoformat(),
        "run_id": run_id,
        "btc_ctx": (ctx or {}).get("btc_snapshot") or {"btc_4h": (ctx or {}).get("btc_4h"), "btc_1h": (ctx or {}).get("btc_1h")},
        "fear_greed": macro.get("fear_greed"),
        "macro_risk": macro.get("risk"),
        "open_count": open_count,
        "closed_count": closed_count,
        "new_signals": [f"{t}:{a}" for t, a in new_signals],
        "paused": paused,
        "top_blockers": dict(reasons.most_common(6)),
    }
    append_event(event, path=path)

"""Persistent active trade state shared by scheduled and manual analyses."""
import json
import os
import uuid
from datetime import datetime, timezone
from admiral_utils import crypto_base
from paper_trading import initialize_paper_tracking

STATE_FILE = "active_signals.json"


def load_positions(path=STATE_FILE):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    normalized = {}
    now = datetime.now(timezone.utc).isoformat()
    for key, value in raw.items():
        if not isinstance(value, dict) or value.get("action") not in ("BUY", "SELL"):
            continue
        signal_id = str(value.get("signal_id") or (key if len(str(key)) == 32 else uuid.uuid4().hex))
        position = dict(value)
        position["signal_id"] = signal_id
        position.setdefault("ticker", key if str(key) != signal_id else "")
        base = crypto_base(position.get("ticker", ""))
        if base:
            position["ticker"] = f"{base}-USDT"
        # Old state was keyed by ticker and had one TP. Retain it as TP1 while
        # assigning conservative R-multiple levels for tracking continuity.
        if "tp1" not in position and position.get("tp") is not None:
            position["tp1"] = position["tp"]
            entry = float(position.get("entry", 0) or 0)
            stop = float(position.get("sl", entry) or entry)
            reward = abs(float(position["tp1"]) - entry)
            if position["action"] == "BUY":
                position.setdefault("tp2", entry + reward * 1.5)
                position.setdefault("tp3", entry + reward * 2.0)
            else:
                position.setdefault("tp2", entry - reward * 1.5)
                position.setdefault("tp3", entry - reward * 2.0)
        if position.get("tp1") is not None:
            entry = float(position.get("entry", 0) or 0)
            reward = abs(float(position["tp1"]) - entry)
            direction = 1 if position["action"] == "BUY" else -1
            position.setdefault("tp2", entry + direction * reward * 1.5)
            position.setdefault("tp3", entry + direction * reward * 2.0)
        for stage in ("tp1", "tp2", "tp3"):
            position.setdefault(f"{stage}_status", "PENDING")
        position.setdefault("source", "watchlist")
        position.setdefault("created_at_utc", now)
        position.setdefault("last_checked_utc", now)
        initialize_paper_tracking(position)
        normalized[signal_id] = position
    return normalized


def save_positions(positions, path=STATE_FILE):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(positions, handle, indent=2)


def _move_stop_to_breakeven(position):
    """After TP1 is banked, protect the remainder by moving the stop to entry."""
    if os.environ.get("ADMIRAL_BREAKEVEN_AFTER_TP1", "1") == "0" or position.get("sl_at_breakeven"):
        return False
    try:
        entry = float(position["entry"])
        old_stop = float(position["sl"])
    except (KeyError, TypeError, ValueError):
        return False
    # Only ever tighten: never move a stop away from price.
    if (position["action"] == "BUY" and entry <= old_stop) or (position["action"] == "SELL" and entry >= old_stop):
        return False
    position["original_sl"] = old_stop
    position["sl"] = entry
    position["sl_at_breakeven"] = True
    return True


def _lock_stop_at_tp1_after_tp2(position):
    """Once TP2 has been banked in an earlier check, trail the stop from entry to TP1 (+1R locked)."""
    if os.environ.get("ADMIRAL_TRAIL_AFTER_TP2", "1") == "0" or position.get("sl_at_tp1"):
        return False
    try:
        tp1 = float(position["tp1"])
        cur_sl = float(position["sl"])
    except (KeyError, TypeError, ValueError):
        return False
    if (position["action"] == "BUY" and tp1 <= cur_sl) or (position["action"] == "SELL" and tp1 >= cur_sl):
        return False
    position.setdefault("original_sl", cur_sl)
    position["sl"] = tp1
    position["sl_at_Breakeven"] = True
    position["sl_at_breakeven"] = True
    position["sl_at_tp1"] = True
    return True


def evaluate_position_range(position, low, high):
    """Apply stop-first conservative touch rules to one observed price range.

    Once TP1 has been banked in an EARLIER check, the stop sits at entry. In the very
    check where TP1 is first reached the original stop still applies (the min/max range
    cannot show whether price pulled back before or after TP1), and the stop moves to
    entry afterwards."""
    if position.get("tp1_status") == "HIT":
        _move_stop_to_breakeven(position)   # also upgrades positions opened before this rule existed
    if position.get("tp2_status") == "HIT":
        _lock_stop_at_tp1_after_tp2(position)
    action = position["action"]
    stop = float(position["sl"])
    stop_hit = low <= stop if action == "BUY" else high >= stop
    newly_hit = []
    stage_values = [position.get("tp1"), position.get("tp2"), position.get("tp3")]
    reached = [high >= float(level) if action == "BUY" else low <= float(level)
               for level in stage_values]
    # If the same observed interval crosses the stop and any target, OHLCV
    # cannot tell which came first; record the conservative stop outcome.
    if stop_hit:
        at_be = bool(position.get("sl_at_breakeven"))
        return {"status": "BE_EXIT" if at_be else "SL_HIT", "closed": True, "stop_hit": True, "newly_hit": [],
                "tp_statuses": [position.get(f"tp{i}_status", "PENDING") for i in (1, 2, 3)],
                "exit_price": stop, "ambiguous": any(reached) and not at_be}
    statuses = []
    for index, did_reach in enumerate(reached, start=1):
        key = f"tp{index}_status"
        old = position.get(key, "PENDING")
        if did_reach and old != "HIT":
            newly_hit.append(index)
            old = "HIT"
        position[key] = old
        statuses.append(old)
    if statuses[2] == "HIT":
        return {"status": "TP3_HIT", "closed": True, "stop_hit": False, "newly_hit": newly_hit,
                "tp_statuses": statuses, "exit_price": stage_values[2], "ambiguous": False}
    moved = False
    if statuses[0] == "HIT" and not position.get("sl_at_breakeven"):
        moved = _move_stop_to_breakeven(position)
    status = "TP2_HIT" if statuses[1] == "HIT" else "TP1_HIT" if statuses[0] == "HIT" else "PENDING"
    return {"status": status, "closed": False, "stop_hit": False, "newly_hit": newly_hit,
            "tp_statuses": statuses, "exit_price": None, "ambiguous": False, "moved_to_breakeven": moved}
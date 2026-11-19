"""Persistent engine state: cooldowns, circuit breaker, closed-trade log, portfolio caps."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

STATE_FILE = "engine_state.json"
TRADES_FILE = "closed_trades.jsonl"


def _f(name: str, default: float) -> float:
    try:
        raw = os.getenv(name, "").strip()
        return float(raw) if raw else float(default)
    except ValueError:
        return float(default)


MAX_OPEN = int(_f("ADMIRAL_MAX_OPEN", 8))
MAX_SAME_DIRECTION = int(_f("ADMIRAL_MAX_SAME_DIRECTION", 3))
COOLDOWN_HOURS = _f("ADMIRAL_COOLDOWN_HOURS", 12)
TP_COOLDOWN_HOURS = _f("ADMIRAL_TP_COOLDOWN_HOURS", 4)
PAUSE_AFTER_LOSSES = int(_f("ADMIRAL_PAUSE_AFTER_LOSSES", 3))
PAUSE_HOURS = _f("ADMIRAL_PAUSE_HOURS", 12)


def _parse(value):
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def load_state(path: str = STATE_FILE) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            state = json.load(fh)
        if not isinstance(state, dict):
            state = {}
    except (OSError, ValueError):
        state = {}
    state.setdefault("cooldown", {})
    state.setdefault("paused_until", "")
    state.setdefault("loss_streak", 0)
    state.setdefault("last_heartbeat_date", "")
    state.setdefault("last_feed_alert_utc", "")
    return state


def save_state(state: dict, path: str = STATE_FILE) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2, sort_keys=True)


def paused_until(state: dict, now: datetime):
    until = _parse(state.get("paused_until"))
    return until if until and until > now else None


def in_cooldown(state: dict, ticker: str, now: datetime):
    until = _parse(state.get("cooldown", {}).get(ticker))
    return until if until and until > now else None


def portfolio_gate(action: str, ticker: str, positions: dict, state: dict, now: datetime) -> tuple[bool, str]:
    """Rules that stop the bot from stacking one correlated bet or trading while cold."""
    until = paused_until(state, now)
    if until:
        return False, f"circuit breaker active until {until.strftime('%Y-%m-%d %H:%M UTC')} after {PAUSE_AFTER_LOSSES} losses in a row"
    cd = in_cooldown(state, ticker, now)
    if cd:
        return False, f"{ticker} is cooling down after a stop-loss until {cd.strftime('%H:%M UTC')}"
    if any(p.get("ticker") == ticker for p in positions.values()):
        return False, f"{ticker} already has an open position"
    if len(positions) >= MAX_OPEN:
        return False, f"{len(positions)} positions already open (max {MAX_OPEN})"
    same = sum(1 for p in positions.values() if p.get("action") == action)
    if same >= MAX_SAME_DIRECTION:
        return False, f"{same} {action} positions already open (max {MAX_SAME_DIRECTION}; majors move together)"
    return True, ""


def r_multiple(position: dict) -> float | None:
    try:
        entry = float(position["entry"])
        stop = float(position.get("original_sl") or position["sl"])
        notional = float(position.get("paper_notional_usdt") or 100.0)
        risk_usdt = notional * abs(entry - stop) / entry
        return float(position.get("paper_total_pnl_usdt", 0.0)) / risk_usdt if risk_usdt > 0 else None
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return None


def record_close(state: dict, position: dict, status: str, now: datetime, path: str = TRADES_FILE) -> None:
    """Update cooldown + loss streak and append the trade to the closed-trade log."""
    r = r_multiple(position)
    ticker = position.get("ticker", "")
    if status == "SL_HIT":
        state["cooldown"][ticker] = (now + timedelta(hours=COOLDOWN_HOURS)).isoformat()
        state["loss_streak"] = int(state.get("loss_streak", 0)) + 1
    elif status in ("TP3_HIT", "BE_EXIT", "TIME_EXIT") and TP_COOLDOWN_HOURS > 0:
        state["cooldown"][ticker] = (now + timedelta(hours=TP_COOLDOWN_HOURS)).isoformat()
        if r is not None and r > 0:
            state["loss_streak"] = 0
    elif r is not None and r > 0:
        state["loss_streak"] = 0
    if state["loss_streak"] >= PAUSE_AFTER_LOSSES:
        state["paused_until"] = (now + timedelta(hours=PAUSE_HOURS)).isoformat()
        state["loss_streak"] = 0
        state["just_paused"] = True
    # forget expired cooldowns so the file stays small
    state["cooldown"] = {k: v for k, v in state["cooldown"].items() if (_parse(v) or now) > now}
    sig_id = str(position.get("signal_id") or "")
    row = {"closed_utc": now.isoformat(), "signal_id": sig_id, "ticker": ticker, "action": position.get("action"), "status": status,
           "entry": position.get("entry"), "exit_price": position.get("exit_price") or position.get("sl"),
           "score": position.get("score"), "setup": position.get("setup", ""),
           "grade": position.get("grade", ""), "r": round(r, 3) if r is not None else None,
           "pnl_usdt": round(float(position.get("paper_total_pnl_usdt", 0.0) or 0.0), 4),
           "opened_utc": position.get("created_at_utc")}
    try:
        already = False
        if sig_id and os.path.exists(path):
            with open(path, "r", encoding="utf-8") as fh:
                for line in fh:
                    if sig_id in line:
                        already = True
                        break
        if not already:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row) + "\n")
    except OSError as exc:
        print(f"[!] could not append {path}: {exc}")


def recent_stats(days: int = 7, path: str = TRADES_FILE, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    rows = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                when = _parse(row.get("closed_utc"))
                if when and now - when <= timedelta(days=days):
                    rows.append(row)
    except OSError:
        pass
    rs = [r["r"] for r in rows if isinstance(r.get("r"), (int, float))]
    return {"trades": len(rows), "wins": sum(1 for r in rs if r > 0), "total_r": round(sum(rs), 2)}

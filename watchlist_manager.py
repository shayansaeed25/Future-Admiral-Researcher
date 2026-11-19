"""
watchlist_manager.py
---------------------
watchlist.json  -> the list of coins + how often (interval_hours) each one
                    should get a fresh deep analysis.
watchlist_state.json -> when each coin was LAST deep-analyzed (so the
                    interval is honored in real wall-clock time, even if
                    the GitHub Actions cron itself fires more often than
                    that -- e.g. cron runs every 4h but a coin is set to
                    interval_hours=6, it will only actually re-analyze
                    once every 6h; every run in between just re-checks
                    that coin's active SL/TP, it does not skip that part).
"""

import json
import os
from datetime import datetime, timezone
from admiral_utils import crypto_base

WATCHLIST_FILE = "watchlist.json"
STATE_FILE = "watchlist_state.json"


def load_watchlist():
    """Returns a list of {"ticker": str, "interval_hours": float}."""
    if not os.path.exists(WATCHLIST_FILE):
        raise FileNotFoundError(
            f"{WATCHLIST_FILE} not found. Create it with a 'coins' list, "
            f"each entry: {{\"ticker\": \"BTC-USDT\", \"interval_hours\": 3}}"
        )
    with open(WATCHLIST_FILE, "r") as f:
        data = json.load(f)
    coins = data.get("coins", [])
    cleaned = []
    for c in coins:
        ticker = c.get("ticker", "").strip().upper()
        if ticker:
            base = crypto_base(ticker)
            if not base:
                base = ticker.replace("-", "").replace("/", "")
            ticker = f"{base}-USDT"
        interval = c.get("interval_hours", 3)
        if not ticker:
            continue
        cleaned.append({"ticker": ticker, "interval_hours": float(interval)})
    return cleaned


def _load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def is_due(ticker, interval_hours, state=None):
    """True if this ticker has never been analyzed, or its interval has
    elapsed since the last analysis."""
    state = state if state is not None else _load_state()
    last = state.get(ticker)
    if not last:
        return True
    try:
        last_dt = datetime.fromisoformat(last)
    except Exception:
        return True
    if last_dt.tzinfo is None:
        last_dt = last_dt.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    interval_seconds = max(1, int(float(interval_hours) * 3600))
    elapsed = (now - last_dt.astimezone(timezone.utc)).total_seconds()
    # The slot check follows the scheduled cadence without drifting by analysis
    # duration. A 30-minute minimum cooldown also blocks back-to-back manual or
    # queued runs that happen to straddle a slot boundary.
    cooldown_seconds = min(interval_seconds, 1800)
    if elapsed < cooldown_seconds:
        return False
    now_slot = int(now.timestamp() // interval_seconds)
    last_slot = int(last_dt.timestamp() // interval_seconds)
    return now_slot > last_slot


def mark_analyzed(ticker):
    """Record a successful completed analysis for the coin's next interval."""
    state = _load_state()
    state[ticker] = datetime.now(timezone.utc).isoformat()
    _save_state(state)


def load_watchlist_and_state():
    return load_watchlist(), _load_state()

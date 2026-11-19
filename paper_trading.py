"""Fixed-notional paper-trade accounting for research signals.

These are estimates based on observed candles, not exchange fills. Gross P&L
excludes trading fees, spread, slippage, funding, and taxes. The paper position
is split equally across TP1/TP2/TP3; any remainder exits at the stop.
"""
from __future__ import annotations

import math
import os
from typing import Any


def configured_notional() -> float:
    try:
        value = float(os.getenv("PAPER_TRADE_NOTIONAL_USDT", "100").strip())
        return value if math.isfinite(value) and value > 0 else 100.0
    except (TypeError, ValueError):
        return 100.0


def _positive(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) and number >= 0 else default
    except (TypeError, ValueError):
        return default


def initialize_paper_tracking(position: dict[str, Any], notional: float | None = None) -> dict[str, Any]:
    """Attach fixed-$100 tracking; infer already-hit TP tranches for old state."""
    entry = _positive(position.get("entry"))
    if entry <= 0 or position.get("action") not in ("BUY", "SELL"):
        return position
    if "paper_initial_qty" in position and "paper_remaining_qty" in position:
        return mark_to_market(position, position.get("last_price", entry))

    amount = _positive(notional if notional is not None else position.get("paper_notional_usdt"), configured_notional())
    if amount <= 0:
        amount = configured_notional()
    initial_qty = amount / entry
    direction = 1.0 if position["action"] == "BUY" else -1.0
    tranche_qty = initial_qty / 3.0
    realized = 0.0
    hit_count = 0
    for stage in (1, 2, 3):
        if str(position.get(f"tp{stage}_status", "PENDING")).upper() == "HIT":
            target = _positive(position.get(f"tp{stage}"))
            if target > 0:
                realized += tranche_qty * (target - entry) * direction
                hit_count += 1
    position.setdefault("paper_notional_usdt", amount)
    position.setdefault("paper_initial_qty", initial_qty)
    position.setdefault("paper_remaining_qty", max(0.0, initial_qty - tranche_qty * hit_count))
    position.setdefault("paper_realized_pnl_usdt", realized)
    position.setdefault("paper_pnl_method", "$100 paper notional; equal 1/3 exits at TP1/TP2/TP3; gross, fees excluded")
    return mark_to_market(position, position.get("last_price", entry))


def mark_to_market(position: dict[str, Any], price: Any) -> dict[str, Any]:
    entry = _positive(position.get("entry"))
    current = _positive(price, entry)
    if entry <= 0:
        return position
    direction = 1.0 if position.get("action") == "BUY" else -1.0
    remaining = _positive(position.get("paper_remaining_qty"))
    realized = float(position.get("paper_realized_pnl_usdt", 0.0) or 0.0)
    unrealized = remaining * (current - entry) * direction
    total = realized + unrealized
    notional = _positive(position.get("paper_notional_usdt"), configured_notional())
    position.update({
        "last_price": current,
        "paper_unrealized_pnl_usdt": unrealized,
        "paper_total_pnl_usdt": total,
        "paper_return_pct": (total / notional) if notional else 0.0,
    })
    return position


def apply_paper_outcome(position: dict[str, Any], outcome: dict[str, Any], mark_price: float) -> dict[str, Any]:
    """Realize equal TP tranches, then close any remainder at SL when hit."""
    initialize_paper_tracking(position)
    entry = _positive(position.get("entry"))
    direction = 1.0 if position.get("action") == "BUY" else -1.0
    initial_qty = _positive(position.get("paper_initial_qty"))
    remaining = _positive(position.get("paper_remaining_qty"))
    realized = float(position.get("paper_realized_pnl_usdt", 0.0) or 0.0)

    if outcome.get("stop_hit"):
        stop = _positive(position.get("sl"), entry)
        realized += remaining * (stop - entry) * direction
        remaining = 0.0
        mark_price = stop
    else:
        for stage in outcome.get("newly_hit", []):
            target = _positive(position.get(f"tp{int(stage)}"))
            tranche = min(remaining, initial_qty / 3.0)
            realized += tranche * (target - entry) * direction
            remaining = max(0.0, remaining - tranche)
        if remaining <= initial_qty * 1e-12:
            remaining = 0.0

    position["paper_realized_pnl_usdt"] = realized
    position["paper_remaining_qty"] = remaining
    return mark_to_market(position, mark_price)


def paper_signal_fields(entry: Any, action: str, notional: float | None = None) -> dict[str, Any]:
    position: dict[str, Any] = {
        "entry": entry,
        "action": action,
        "tp1_status": "PENDING",
        "tp2_status": "PENDING",
        "tp3_status": "PENDING",
    }
    if action not in ("BUY", "SELL"):
        return {}
    initialize_paper_tracking(position, notional)
    return {key: position[key] for key in (
        "paper_notional_usdt", "paper_initial_qty", "paper_remaining_qty",
        "paper_realized_pnl_usdt", "paper_unrealized_pnl_usdt",
        "paper_total_pnl_usdt", "paper_return_pct", "paper_pnl_method",
    )}

"""Deterministic, auditable crypto derivatives score (LLM-independent)."""
from __future__ import annotations

from typing import Any


# Give more weight to executed taker flow and directional OI; a top-20 book
# snapshot is especially easy to spoof and is only a small supporting factor.
WEIGHTS = {"cvd": 0.30, "orderbook_imbalance": 0.10, "funding": 0.15, "open_interest": 0.25, "liquidations": 0.20}


def _number(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(str(value).replace("%", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def score_derivatives(data: dict[str, Any]) -> dict[str, Any]:
    """Map available factors to [-1,1], then apply fixed weights.

    Missing values are excluded from the denominator and lower coverage. A
    directional call requires >=60% weight coverage and score magnitude >=.20.
    """
    components: dict[str, float | None] = {}
    ratio = _number(data.get("taker_buy_sell_ratio"))
    components["cvd"] = max(-1.0, min(1.0, (ratio - 1.0) / (ratio + 1.0))) if ratio is not None and ratio >= 0 else None
    wall = _number(data.get("wall_imbalance"))
    # A public top-of-book snapshot cannot identify whale orders; label it as
    # the measurable proxy it is, rather than implying wallet/entity identity.
    components["orderbook_imbalance"] = max(-1.0, min(1.0, wall)) if wall is not None else None
    funding = _number(data.get("funding_rate"))
    components["funding"] = max(-1.0, min(1.0, -funding / 0.03)) if funding is not None else None
    oi_change = _number(data.get("open_interest_change_pct"))
    cvd = components["cvd"]
    # OI growth without a directional volume signal does not say whether longs
    # or shorts are being added, so leave the factor unscored in that case.
    components["open_interest"] = max(-1.0, min(1.0, (oi_change / 10) * (1 if cvd >= 0 else -1))) if oi_change is not None and cvd is not None else None
    liq = _number(data.get("liquidation_imbalance"))
    components["liquidations"] = max(-1.0, min(1.0, liq)) if liq is not None else None
    observed = {k: v for k, v in components.items() if v is not None}
    coverage = sum(WEIGHTS[k] for k in observed)
    score = sum(WEIGHTS[k] * v for k, v in observed.items()) / coverage if coverage else 0.0
    action = "BUY" if coverage >= 0.60 and score >= 0.20 else "SELL" if coverage >= 0.60 and score <= -0.20 else "NO TRADE"
    return {"score": round(score, 4), "coverage": round(coverage, 4), "action": action,
            "components": components, "weights": WEIGHTS.copy(), "version": "1.1"}

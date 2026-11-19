"""Deterministic SMC zones and multi-timeframe candles for crypto markets."""
from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

import requests
from .symbol_utils import crypto_base



def _symbol(ticker: str) -> str:
    """Normalize any USD/USDT/USDC pair without substring damage."""
    base = crypto_base(ticker)
    if not base:
        raise ValueError(f"Cannot normalize crypto candle symbol: {ticker!r}")
    return f"{base}USDT"


_OKX_BARS = {"1h": "1H", "4h": "4H", "1d": "1D"}


def _okx_candles(symbol: str, interval: str, limit: int) -> list[list[Any]]:
    """Fallback candles via OKX public REST, reshaped to Binance's row order
    ([ts, open, high, low, close, ...]) so callers don't need to know which
    venue answered."""
    bar = _OKX_BARS.get(interval)
    if not bar:
        raise ValueError(f"No OKX bar mapping for interval {interval}")
    base = symbol[:-4] if symbol.endswith("USDT") else symbol
    errors = []
    for inst_id in (f"{base}-USDT-SWAP", f"{base}-USDT"):
        try:
            response = requests.get(
                "https://www.okx.com/api/v5/market/candles",
                params={"instId": inst_id, "bar": bar, "limit": min(limit, 300)}, timeout=8,
            )
            response.raise_for_status()
            payload = response.json()
            rows = payload.get("data") or []
            if not rows:
                raise ValueError(f"OKX {payload.get('code')}: {payload.get('msg', 'no candle rows')}")
            rows = list(reversed(rows))  # OKX returns newest-first; Binance format is chronological
            return [[r[0], r[1], r[2], r[3], r[4], r[5]] for r in rows]
        except Exception as exc:
            errors.append(f"{inst_id}: {' '.join(str(exc).split())[:130]}")
    raise ValueError("OKX swap and spot candle feeds failed: " + " | ".join(errors))


def _candles(symbol: str, interval: str, limit: int = 120) -> list[list[Any]]:
    # Use OKX public swap first, then spot for symbols without a swap market.
    return _okx_candles(symbol, interval, limit)


def _swing_pool_levels(bars: list[dict[str, float]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Cluster repeated fractal swing highs/lows as rough stop-pool proxies."""
    if len(bars) < 7:
        return [], []
    close = bars[-1]["c"]
    typical_range = sum(bar["h"] - bar["l"] for bar in bars[-14:]) / min(14, len(bars))
    tolerance = max(close * 0.001, typical_range * 0.35)
    swing_highs, swing_lows = [], []
    for index in range(2, len(bars) - 2):
        bar = bars[index]
        neighbors = bars[index - 2:index] + bars[index + 1:index + 3]
        weight = max(bar.get("v", 0.0), 1.0)
        if bar["h"] > max(item["h"] for item in neighbors):
            swing_highs.append((bar["h"], weight))
        if bar["l"] < min(item["l"] for item in neighbors):
            swing_lows.append((bar["l"], weight))

    def cluster(points):
        groups: list[list[tuple[float, float]]] = []
        for point in sorted(points):
            if groups and point[0] - groups[-1][-1][0] <= tolerance:
                groups[-1].append(point)
            else:
                groups.append([point])
        levels = []
        for group in groups:
            level = sum(price * weight for price, weight in group) / sum(weight for _, weight in group)
            levels.append({"level": level, "touches": len(group), "tolerance": tolerance})
        return levels

    highs = [pool for pool in cluster(swing_highs) if pool["level"] > close]
    lows = [pool for pool in cluster(swing_lows) if pool["level"] < close]
    # If the sampled candles contain no fractal pivots on one side, expose the
    # recent range extreme as a single-touch fallback and keep it labeled as an estimate.
    if not highs:
        recent = max(bar["h"] for bar in bars[-30:-1])
        if recent > close:
            highs = [{"level": recent, "touches": 1, "tolerance": tolerance}]
    if not lows:
        recent = min(bar["l"] for bar in bars[-30:-1])
        if 0 < recent < close:
            lows = [{"level": recent, "touches": 1, "tolerance": tolerance}]
    return highs, lows


def _ohlcv_context(bars: list[dict[str, float]]) -> dict[str, Any]:
    """Compute transparent trend/momentum/accumulation proxies from candles."""
    closes = [bar["c"] for bar in bars]
    last = bars[-1]
    sma20 = sum(closes[-20:]) / 20 if len(closes) >= 20 else None
    sma50 = sum(closes[-50:]) / 50 if len(closes) >= 50 else None
    changes = [closes[index] - closes[index - 1] for index in range(len(closes) - 14, len(closes))]
    gains = sum(max(change, 0.0) for change in changes) / 14
    losses = sum(max(-change, 0.0) for change in changes) / 14
    rsi = 100.0 if losses == 0 and gains > 0 else 50.0 if losses == 0 else 100 - 100 / (1 + gains / losses)
    return_5 = (closes[-1] / closes[-6] - 1) * 100 if len(closes) >= 6 and closes[-6] else 0.0

    volumes = [max(float(bar.get("v", 0.0)), 0.0) for bar in bars]
    money_flow_volume = 0.0
    for bar in bars[-20:]:
        candle_range = bar["h"] - bar["l"]
        multiplier = ((2 * bar["c"] - bar["h"] - bar["l"]) / candle_range) if candle_range > 0 else 0.0
        money_flow_volume += multiplier * max(float(bar.get("v", 0.0)), 0.0)
    total_volume = sum(volumes[-20:])
    cmf = money_flow_volume / total_volume if total_volume > 0 else None

    obv = 0.0
    obv_series = [0.0]
    for index in range(1, len(bars)):
        if closes[index] > closes[index - 1]:
            obv += volumes[index]
        elif closes[index] < closes[index - 1]:
            obv -= volumes[index]
        obv_series.append(obv)
    obv_change = obv_series[-1] - obv_series[-6] if len(obv_series) >= 6 else 0.0
    recent_volume = sum(volumes[-5:]) / 5 if len(volumes) >= 5 else None
    prior_volume = sum(volumes[-25:-5]) / 20 if len(volumes) >= 25 else None
    volume_ratio = recent_volume / prior_volume if recent_volume is not None and prior_volume else None

    if sma20 is not None and sma50 is not None:
        if last["c"] > sma20 and sma20 > sma50:
            trend = "bullish"
        elif last["c"] < sma20 and sma20 < sma50:
            trend = "bearish"
        else:
            trend = "mixed"
    else:
        trend = "bullish" if sma20 is not None and last["c"] > sma20 else "bearish" if sma20 is not None and last["c"] < sma20 else "neutral"
    if cmf is not None and cmf >= 0.05 and obv_change > 0:
        accumulation = "ACCUMULATION"
    elif cmf is not None and cmf <= -0.05 and obv_change < 0:
        accumulation = "DISTRIBUTION"
    elif cmf is not None:
        accumulation = "MIXED"
    else:
        accumulation = "UNAVAILABLE"
    momentum = "bullish" if rsi >= 55 and return_5 > 0 else "bearish" if rsi <= 45 and return_5 < 0 else "mixed"
    return {
        "trend": trend, "sma20": sma20, "sma50": sma50, "rsi14": round(rsi, 2),
        "return_5_candles_pct": round(return_5, 3), "momentum": momentum,
        "cmf20": round(cmf, 4) if cmf is not None else None,
        "obv_change_5_candles": round(obv_change, 3),
        "volume_expansion_ratio": round(volume_ratio, 3) if volume_ratio is not None else None,
        "accumulation_distribution": accumulation,
        "method": "OHLCV-derived CMF/OBV/RSI/SMA proxies; not wallet identity or verified institution activity",
    }


def _overall_regime(timeframes: dict[str, Any]) -> dict[str, Any]:
    weights = {"1h": 1.0, "4h": 1.5, "1d": 2.0}
    available = [(tf, values) for tf, values in timeframes.items() if values.get("trend") not in (None, "unavailable")]
    if not available:
        return {"status": "UNAVAILABLE", "coverage": "0/3", "trend": "UNAVAILABLE",
                "momentum": "UNAVAILABLE", "accumulation_distribution": "UNAVAILABLE"}
    trend_score = 0.0
    momentum_score = 0.0
    for tf, values in available:
        weight = weights.get(tf, 1.0)
        trend_score += weight * (1 if values.get("trend") == "bullish" else -1 if values.get("trend") == "bearish" else 0)
        momentum = values.get("momentum")
        if momentum in ("bullish", "bearish"):
            momentum_score += weight * (1 if momentum == "bullish" else -1)
    trend = "BULLISH" if trend_score >= 1.5 else "BEARISH" if trend_score <= -1.5 else "MIXED / RANGE"
    momentum = "BULLISH" if momentum_score > 0.25 else "BEARISH" if momentum_score < -0.25 else "MIXED"
    states = [item.get("accumulation_distribution") for _, item in available]
    acc = "ACCUMULATION" if states.count("ACCUMULATION") >= 2 else "DISTRIBUTION" if states.count("DISTRIBUTION") >= 2 else "MIXED"
    return {
        "status": "OK" if len(available) == 3 else "PARTIAL", "coverage": f"{len(available)}/3",
        "trend": trend, "trend_score": round(trend_score, 2), "momentum": momentum,
        "momentum_score": round(momentum_score, 2), "accumulation_distribution": acc,
        "timeframe_trends": {tf: values.get("trend", "unavailable").upper() for tf, values in timeframes.items()},
        "timeframe_flows": {tf: values.get("accumulation_distribution", "UNAVAILABLE") for tf, values in timeframes.items()},
        "note": "Rule-based OHLCV context only. It is not predictive certainty and cannot identify whales/institutions.",
    }


@lru_cache(maxsize=32)
def analyze_smc_timeframes(ticker: str) -> dict[str, Any]:
    """Return 1h/4h/1d trend plus recent OB, FVG and liquidity zones.

    Zones are rule-based approximations from OHLCV; they are not exchange order
    book data and are deliberately labeled as estimates.
    """
    result: dict[str, Any] = {"symbol": ticker, "timeframes": {}, "status": "ok"}
    errors = []
    for tf in ("1h", "4h", "1d"):
        try:
            rows = _candles(_symbol(ticker), tf)
            bars = [{"o": float(r[1]), "h": float(r[2]), "l": float(r[3]), "c": float(r[4]), "v": float(r[5])} for r in rows]
            if len(bars) < 30:
                raise ValueError("Insufficient candles")
            closes = [b["c"] for b in bars]
            last = bars[-1]
            context = _ohlcv_context(bars)
            trend = context["trend"]
            fvg = []
            order_blocks = []
            for i in range(2, len(bars)):
                left, middle, right = bars[i - 2], bars[i - 1], bars[i]
                if right["l"] > left["h"]:
                    fvg.append({"side": "bullish", "low": left["h"], "high": right["l"]})
                elif right["h"] < left["l"]:
                    fvg.append({"side": "bearish", "low": right["h"], "high": left["l"]})
                impulse = abs(middle["c"] - middle["o"]) / max(middle["o"], 1e-12)
                if impulse >= 0.008:
                    prior = bars[i - 2]
                    if middle["c"] > middle["o"] and prior["c"] < prior["o"]:
                        order_blocks.append({"side": "bullish", "low": prior["l"], "high": prior["h"]})
                    elif middle["c"] < middle["o"] and prior["c"] > prior["o"]:
                        order_blocks.append({"side": "bearish", "low": prior["l"], "high": prior["h"]})
            high_pools, low_pools = _swing_pool_levels(bars)
            nearest_high = min(high_pools, key=lambda pool: pool["level"] - last["c"], default=None)
            nearest_low = min(low_pools, key=lambda pool: last["c"] - pool["level"], default=None)
            result["timeframes"][tf] = {
                "last_close": last["c"], "trend": trend, **context,
                "liquidity_above": nearest_high["level"] if nearest_high else None,
                "liquidity_above_touches": nearest_high["touches"] if nearest_high else 0,
                "liquidity_below": nearest_low["level"] if nearest_low else None,
                "liquidity_below_touches": nearest_low["touches"] if nearest_low else 0,
                "range_high": max(b["h"] for b in bars[-60:]), "range_low": min(b["l"] for b in bars[-60:]),
                "atr14": sum(max(bars[k]["h"] - bars[k]["l"], abs(bars[k]["h"] - bars[k - 1]["c"]), abs(bars[k]["l"] - bars[k - 1]["c"])) for k in range(len(bars) - 14, len(bars))) / 14,
                "order_blocks": order_blocks[-3:], "fair_value_gaps": fvg[-3:],
                "method": "Repeated fractal swing clusters from OHLCV; estimated stop/liquidation pools, not exchange liquidation data",
            }
        except Exception as exc:
            errors.append(f"{tf}: {exc}")
    if errors:
        result["status"] = "partial" if result["timeframes"] else "unavailable"
        result["errors"] = errors
    result["market_regime"] = _overall_regime(result["timeframes"])
    return result


def format_smc_context(ticker: str) -> str:
    """Compact report intended for inclusion in a research prompt."""
    data = analyze_smc_timeframes(ticker)
    if not data["timeframes"]:
        return "SMC/multi-timeframe data unavailable: " + "; ".join(data.get("errors", []))
    lines = ["Rule-based SMC and multi-timeframe snapshot (zones and flow labels are OHLCV-derived estimates):"]
    regime = data.get("market_regime", {})
    lines.append(
        f"Overall market regime: trend={regime.get('trend', 'UNAVAILABLE')}, "
        f"momentum={regime.get('momentum', 'UNAVAILABLE')}, "
        f"accumulation/distribution={regime.get('accumulation_distribution', 'UNAVAILABLE')}, "
        f"timeframe coverage={regime.get('coverage', '0/3')}."
    )
    for tf, item in data["timeframes"].items():
        above = f"{item['liquidity_above']:.8g}" if item.get("liquidity_above") is not None else "no estimate"
        below = f"{item['liquidity_below']:.8g}" if item.get("liquidity_below") is not None else "no estimate"
        lines.append(
            f"{tf}: trend={item['trend']}, momentum={item.get('momentum')}, "
            f"A/D={item.get('accumulation_distribution')}, RSI14={item.get('rsi14')}, "
            f"CMF20={item.get('cmf20') if item.get('cmf20') is not None else 'n/a'}, "
            f"5-candle return={item.get('return_5_candles_pct')}%, close={item['last_close']:.8g}, "
            f"estimated liquidity above/below={above}/{below}; OB={item['order_blocks']}; FVG={item['fair_value_gaps']}"
        )
    if data.get("errors"):
        lines.append("Unavailable timeframes: " + "; ".join(data["errors"]))
    return "\n".join(lines)


def liquidity_aware_levels(
    symbol: str,
    side: str,
    entry_price: float,
    atr: Optional[float] = None,
    min_rr: float = 1.5,
    market_data: Optional[Dict[str, Any]] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Calculate institutional Whale SL and TP1/TP2/TP3 from multi-timeframe swing liquidity pools & Order Blocks.

    Key v3 Improvements:
    - Places Stop Loss beyond the structural 1H/4H whale liquidity pool or Order Block + ATR buffer (preventing premature stop-outs on wicks while keeping risk capped between 0.6% and 4.2%).
    - Filters Take Profit liquidity pools so TP1 is ALWAYS at least >= 1.25R (skipping minor 1H noise swings that are too close to entry, which previously caused 'The nearest estimated levels do not meet the minimum reward-to-risk').
    - Ensures average Reward-to-Risk across TP1/TP2/TP3 meets or exceeds min_rr.
    """
    side_u = (side or "").upper()
    if side_u not in ("BUY", "SELL") or not entry_price or entry_price <= 0:
        return {"available": False, "reason": "invalid side or entry"}

    tf_1h = _analyze_tf(symbol, "1H", 120)
    tf_4h = _analyze_tf(symbol, "4H", 120)
    tf_1d = _analyze_tf(symbol, "1D", 90)
    tfs = [t for t in (tf_1h, tf_4h, tf_1d) if t.get("available")]
    if not tfs:
        return {"available": False, "reason": "no OHLCV timeframes available"}

    atr_candidates = [float(t["atr"]) for t in tfs if t.get("atr")]
    base_atr = float(atr) if (atr and atr > 0) else (atr_candidates[0] if atr_candidates else entry_price * 0.015)
    atr_1h = float(tf_1h.get("atr") or base_atr)
    atr_4h = float(tf_4h.get("atr") or base_atr * 1.6)
    buffer = max(entry_price * 0.0025, 0.35 * atr_1h)

    below_pools: List[float] = []
    above_pools: List[float] = []
    for t in tfs:
        for p in t.get("equal_lows", []) + t.get("swing_lows", []):
            if p and p < entry_price:
                below_pools.append(float(p))
        for p in t.get("equal_highs", []) + t.get("swing_highs", []):
            if p and p > entry_price:
                above_pools.append(float(p))
        ob = t.get("bullish_ob")
        if ob and ob.get("bottom") and float(ob["bottom"]) < entry_price:
            below_pools.append(float(ob["bottom"]))
        beb = t.get("bearish_ob")
        if beb and beb.get("top") and float(beb["top"]) > entry_price:
            above_pools.append(float(beb["top"]))

    below_pools = sorted(set(below_pools), reverse=True)  # closest below first
    above_pools = sorted(set(above_pools))                # closest above first

    min_risk = max(entry_price * 0.006, 0.75 * atr_1h)
    max_risk = min(entry_price * 0.042, 2.6 * atr_4h)

    if side_u == "BUY":
        # Choose a structural support pool below entry that gives at least min_risk breathing room
        valid_sl_pools = [p for p in below_pools if (entry_price - (p - buffer)) >= min_risk]
        if valid_sl_pools:
            raw_sl = valid_sl_pools[0] - buffer
        elif below_pools:
            raw_sl = below_pools[0] - max(buffer, min_risk * 0.6)
        else:
            raw_sl = entry_price - max(min_risk, 1.35 * atr_1h)

        risk = entry_price - raw_sl
        if risk < min_risk:
            risk = min_risk
            raw_sl = entry_price - risk
        elif risk > max_risk:
            risk = max_risk
            raw_sl = entry_price - risk

        # Filter target pools above entry that offer at least 1.25R for TP1
        tp1_candidates = [p for p in above_pools if (p - entry_price) >= 1.25 * risk]
        tp1 = tp1_candidates[0] if tp1_candidates else (entry_price + 1.5 * risk)
        if (tp1 - entry_price) > 3.0 * risk:
            tp1 = entry_price + 1.5 * risk

        tp2_candidates = [p for p in above_pools if (p - entry_price) >= max(2.2 * risk, (tp1 - entry_price) + 0.75 * risk)]
        tp2 = tp2_candidates[0] if tp2_candidates else (entry_price + 2.4 * risk)
        if (tp2 - entry_price) > 4.5 * risk:
            tp2 = entry_price + 2.5 * risk

        tp3_candidates = [p for p in above_pools if (p - entry_price) >= max(3.4 * risk, (tp2 - entry_price) + 0.9 * risk)]
        tp3 = tp3_candidates[0] if tp3_candidates else (entry_price + 3.6 * risk)
        if (tp3 - entry_price) > 6.5 * risk:
            tp3 = entry_price + 3.8 * risk
    else:
        # SELL: choose structural resistance pool above entry with at least min_risk breathing room
        valid_sl_pools = [p for p in above_pools if ((p + buffer) - entry_price) >= min_risk]
        if valid_sl_pools:
            raw_sl = valid_sl_pools[0] + buffer
        elif above_pools:
            raw_sl = above_pools[0] + max(buffer, min_risk * 0.6)
        else:
            raw_sl = entry_price + max(min_risk, 1.35 * atr_1h)

        risk = raw_sl - entry_price
        if risk < min_risk:
            risk = min_risk
            raw_sl = entry_price + risk
        elif risk > max_risk:
            risk = max_risk
            raw_sl = entry_price + risk

        tp1_candidates = [p for p in below_pools if (entry_price - p) >= 1.25 * risk and p > 0]
        tp1 = tp1_candidates[0] if tp1_candidates else max(entry_price * 0.5, entry_price - 1.5 * risk)
        if (entry_price - tp1) > 3.0 * risk:
            tp1 = entry_price - 1.5 * risk

        tp2_candidates = [p for p in below_pools if (entry_price - p) >= max(2.2 * risk, (entry_price - tp1) + 0.75 * risk) and p > 0]
        tp2 = tp2_candidates[0] if tp2_candidates else max(entry_price * 0.4, entry_price - 2.4 * risk)
        if (entry_price - tp2) > 4.5 * risk:
            tp2 = entry_price - 2.5 * risk

        tp3_candidates = [p for p in below_pools if (entry_price - p) >= max(3.4 * risk, (entry_price - tp2) + 0.9 * risk) and p > 0]
        tp3 = tp3_candidates[0] if tp3_candidates else max(entry_price * 0.3, entry_price - 3.6 * risk)
        if (entry_price - tp3) > 6.5 * risk:
            tp3 = entry_price - 3.8 * risk

    rr1 = abs(tp1 - entry_price) / risk if risk > 0 else 0.0
    rr2 = abs(tp2 - entry_price) / risk if risk > 0 else 0.0
    rr3 = abs(tp3 - entry_price) / risk if risk > 0 else 0.0
    avg_rr = (rr1 + rr2 + rr3) / 3.0

    return {
        "available": True,
        "sl": float(raw_sl),
        "tp1": float(tp1),
        "tp2": float(tp2),
        "tp3": float(tp3),
        "risk": float(risk),
        "rr1": round(rr1, 2),
        "rr2": round(rr2, 2),
        "rr3": round(rr3, 2),
        "rr": round(avg_rr, 2),
        "basis": "Whale swing-liquidity pool & Order Block SL with >=1.25R/2.4R/3.6R liquidity targets",
    }

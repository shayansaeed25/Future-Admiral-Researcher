"""Deterministic Future Admiral v4 Signal Engine (1H Institutional + Live Rolling Backtest Filter).

The SAME functions drive the live scanner (``cloud_run.py``), ``analyze_now.py``,
and ``backtest.py`` so what you backtest is what trades.

Core Design (v4.0 — Post-38-Trade Live Audit Upgrade)
-----------------------------------------------------
1. Live Per-Run Rolling Strategy Backtester (``backtest_setup_on_history``):
   On every scan, before any strategy/setup is allowed to issue a live signal on a coin,
   the engine backtests that exact setup across the coin's recent 1H candle history
   (strictly causal, zero look-ahead). Setups with high recent Stop-Loss rates (>50%)
   or negative expectancy are automatically rejected; only high-win-rate, low-SL
   strategies pass.
2. Post-Mortem Risk & Strategy Hardening (from 38-trade live sheet audit):
   - Removed counter-trend knife-catching (``whale_reversal`` removed from default setups;
     trades must align with 4H trend and never fight 1D trend).
   - Raised minimum conviction score gate (default ``ADMIRAL_MIN_SCORE = 70``, matching the
     profitable >=70 score band from live data).
   - Stop-Loss Sweet-Spot Guard (``0.9% <= SL <= 2.5%`` of price): eliminates both ultra-wide
     5%-8% altcoin stops (like UNI/ARB that caused oversized dollar losses on $100 notional)
     and ultra-tight <0.8% noise stops.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import numpy as np


def _env(name: str, default: float) -> float:
    try:
        raw = os.getenv(name, "").strip()
        return float(raw) if raw else float(default)
    except ValueError:
        return float(default)


# --------------------------------------------------------------------------
# Indicators (causal: value at index i only uses bars <= i)
# --------------------------------------------------------------------------

def ema(x: np.ndarray, n: int) -> np.ndarray:
    alpha = 2.0 / (n + 1)
    out = np.empty_like(x, dtype=float)
    out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = alpha * x[i] + (1 - alpha) * out[i - 1]
    return out


def rma(x: np.ndarray, n: int) -> np.ndarray:
    """Wilder smoothing (seeded with the first value)."""
    out = np.empty_like(x, dtype=float)
    out[0] = x[0]
    a = 1.0 / n
    for i in range(1, len(x)):
        out[i] = a * x[i] + (1 - a) * out[i - 1]
    return out


def sma(x: np.ndarray, n: int) -> np.ndarray:
    csum = np.cumsum(np.insert(x, 0, 0.0))
    out = np.empty_like(x, dtype=float)
    for i in range(len(x)):
        lo = max(0, i - n + 1)
        out[i] = (csum[i + 1] - csum[lo]) / (i + 1 - lo)
    return out


def true_range(h, l, c):
    prev = np.concatenate(([c[0]], c[:-1]))
    return np.maximum(h - l, np.maximum(np.abs(h - prev), np.abs(l - prev)))


def rsi(c: np.ndarray, n: int = 14) -> np.ndarray:
    d = np.diff(c, prepend=c[0])
    gain, loss = rma(np.maximum(d, 0), n), rma(np.maximum(-d, 0), n)
    rs = np.divide(gain, loss, out=np.full_like(gain, np.inf), where=loss > 0)
    return np.where(loss > 0, 100 - 100 / (1 + rs), 100.0)


def macd(c: np.ndarray, fast: int = 12, slow: int = 26, sig: int = 9) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    line = ema(c, fast) - ema(c, slow)
    signal = ema(line, sig)
    hist = line - signal
    return line, signal, hist


def rolling_vwap(h: np.ndarray, l: np.ndarray, c: np.ndarray, v: np.ndarray, n: int = 24) -> np.ndarray:
    tp = (h + l + c) / 3.0
    tpv = tp * v
    out = np.empty_like(c, dtype=float)
    for i in range(len(c)):
        lo = max(0, i - n + 1)
        vsum = v[lo:i + 1].sum()
        out[i] = tpv[lo:i + 1].sum() / vsum if vsum > 0 else c[i]
    return out


def obv_zscore(c: np.ndarray, v: np.ndarray) -> np.ndarray:
    """Normalized OBV momentum (-1..+1): positive means stealth volume accumulation."""
    d = np.diff(c, prepend=c[0])
    signed_v = np.where(d > 0, v, np.where(d < 0, -v, 0.0))
    obv = np.cumsum(signed_v)
    fast = ema(obv, 8)
    slow = ema(obv, 21)
    v_avg = sma(v, 20)
    denom = np.where(v_avg > 0, v_avg * 4.0, 1.0)
    return np.clip((fast - slow) / denom, -1.0, 1.0)


def adx(h, l, c, n: int = 14) -> np.ndarray:
    up, down = np.diff(h, prepend=h[0]), -np.diff(l, prepend=l[0])
    plus = np.where((up > down) & (up > 0), up, 0.0)
    minus = np.where((down > up) & (down > 0), down, 0.0)
    atr_ = rma(true_range(h, l, c), n)
    with np.errstate(divide="ignore", invalid="ignore"):
        pdi = 100 * rma(plus, n) / np.where(atr_ > 0, atr_, np.nan)
        mdi = 100 * rma(minus, n) / np.where(atr_ > 0, atr_, np.nan)
        dx = 100 * np.abs(pdi - mdi) / (pdi + mdi)
    return rma(np.nan_to_num(dx), n)


def cmf(h, l, c, v, n: int = 20) -> np.ndarray:
    rng = h - l
    mult = np.divide(2 * c - h - l, rng, out=np.zeros_like(c), where=rng > 0)
    mfv = mult * v
    out = np.zeros_like(c, dtype=float)
    for i in range(len(c)):
        lo = max(0, i - n + 1)
        tot = v[lo:i + 1].sum()
        out[i] = mfv[lo:i + 1].sum() / tot if tot > 0 else 0.0
    return out


@dataclass
class Frame:
    """Pre-computed indicators for one timeframe (all arrays share one index)."""
    ts: np.ndarray
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray
    v: np.ndarray
    ema20: np.ndarray
    ema50: np.ndarray
    atr: np.ndarray
    rsi: np.ndarray
    adx: np.ndarray
    cmf: np.ndarray
    ema9: np.ndarray | None = None
    ema200: np.ndarray | None = None
    macd_line: np.ndarray | None = None
    macd_sig: np.ndarray | None = None
    macd_hist: np.ndarray | None = None
    vwap24: np.ndarray | None = None
    obv_mom: np.ndarray | None = None

    def __len__(self) -> int:
        return len(self.c)


def build_frame(d: dict[str, np.ndarray]) -> Frame:
    o, h, l, c, v = d["o"], d["h"], d["l"], d["c"], d["v"]
    m_line, m_sig, m_hist = macd(c)
    return Frame(
        ts=d["ts"], o=o, h=h, l=l, c=c, v=v,
        ema20=ema(c, 20), ema50=ema(c, 50),
        atr=rma(true_range(h, l, c), 14), rsi=rsi(c), adx=adx(h, l, c), cmf=cmf(h, l, c, v),
        ema9=ema(c, 9), ema200=ema(c, 200),
        macd_line=m_line, macd_sig=m_sig, macd_hist=m_hist,
        vwap24=rolling_vwap(h, l, c, v, 24), obv_mom=obv_zscore(c, v),
    )


# --------------------------------------------------------------------------
# Trend & Institutional Regime helpers
# --------------------------------------------------------------------------

def trend_state(f: Frame, i: int) -> int:
    """+1 bullish, -1 bearish, 0 mixed."""
    if i < 55:
        return 0
    slope = f.ema50[i] - f.ema50[i - 6]
    if f.c[i] > f.ema50[i] and f.ema20[i] > f.ema50[i] and slope > 0:
        return 1
    if f.c[i] < f.ema50[i] and f.ema20[i] < f.ema50[i] and slope < 0:
        return -1
    return 0


def fast_impulse_direction(f: Frame, i: int) -> int:
    """Detects fast 1H institutional directional moves."""
    if i < 30:
        return 0
    e9 = f.ema9[i] if f.ema9 is not None else f.ema20[i]
    vwap = f.vwap24[i] if f.vwap24 is not None else f.ema20[i]
    m_hist = f.macd_hist[i] if f.macd_hist is not None else 0.0
    if f.c[i] > e9 >= f.ema20[i] > f.ema50[i] and f.c[i] > vwap and m_hist > 0 and f.rsi[i] >= 53:
        return 1
    if f.c[i] < e9 <= f.ema20[i] < f.ema50[i] and f.c[i] < vwap and m_hist < 0 and f.rsi[i] <= 47:
        return -1
    return 0


_LABEL = {1: "BULLISH", -1: "BEARISH", 0: "MIXED"}


def regime_summary(f1: Frame, i1: int, f4: Frame, i4: int, fd: Frame, idd: int) -> dict[str, Any]:
    t1, t4, td = trend_state(f1, i1), trend_state(f4, i4), trend_state(fd, idd)
    if t1 == 0:
        t1 = fast_impulse_direction(f1, i1)
    score = 1.0 * t1 + 1.5 * t4 + 2.0 * td
    overall = "BULLISH" if score >= 2.0 else "BEARISH" if score <= -2.0 else "MIXED / RANGE"
    mom = 1 if f1.rsi[i1] >= 54 else -1 if f1.rsi[i1] <= 46 else 0
    c4 = float(f4.cmf[i4])
    obv1 = float(f1.obv_mom[i1]) if f1.obv_mom is not None else 0.0
    flow_state = "ACCUMULATION" if (c4 >= 0.04 or obv1 >= 0.25) else "DISTRIBUTION" if (c4 <= -0.04 or obv1 <= -0.25) else "MIXED"
    return {
        "status": "OK", "coverage": "3/3", "trend": overall, "momentum": _LABEL[mom],
        "accumulation_distribution": flow_state,
        "timeframe_trends": {"1h": _LABEL[t1], "4h": _LABEL[t4], "1d": _LABEL[td]},
        "adx_4h": round(float(f4.adx[i4]), 1), "rsi_1h": round(float(f1.rsi[i1]), 1), "cmf_4h": round(c4, 3),
    }


# --------------------------------------------------------------------------
# Setups on the 1H timeframe
# --------------------------------------------------------------------------

def _pullback(f: Frame, i: int, d: int) -> dict | None:
    """Trend continuation: price dipped to the 1H EMA20 zone and closed back with the trend."""
    if i < 60:
        return None
    a = f.atr[i]
    w = slice(i - 7, i + 1)
    if d == 1:
        touched = f.l[w].min() <= f.ema20[i] + 0.25 * a
        shallow = f.l[w].min() >= f.ema50[i] - 0.5 * a
        reclaim = f.c[i] > f.ema20[i] and f.c[i] > f.o[i]
        extended = f.c[i] - f.ema20[i] > 1.5 * a
        rsi_ok = 40 <= f.rsi[i] <= 68
        aligned = f.ema20[i] > f.ema50[i]
        deep = f.l[w].min() <= f.ema50[i] + 0.25 * a
    else:
        touched = f.h[w].max() >= f.ema20[i] - 0.25 * a
        shallow = f.h[w].max() <= f.ema50[i] + 0.5 * a
        reclaim = f.c[i] < f.ema20[i] and f.c[i] < f.o[i]
        extended = f.ema20[i] - f.c[i] > 1.5 * a
        rsi_ok = 32 <= f.rsi[i] <= 60
        aligned = f.ema20[i] < f.ema50[i]
        deep = f.h[w].max() >= f.ema50[i] - 0.25 * a
    if touched and shallow and reclaim and not extended and rsi_ok and aligned:
        return {"name": "pullback", "quality": 12 + (5 if deep else 0),
                "note": "pullback to 1H EMA20 reclaimed" + (" (EMA50 zone tested)" if deep else "")}
    return None


def _breakout(f: Frame, i: int, d: int) -> dict | None:
    """Break of the 24-bar range with expanding volume and a strong candle body."""
    if i < 60:
        return None
    a = f.atr[i]
    rng = f.h[i] - f.l[i]
    vavg = f.v[i - 20:i].mean()
    vol_ratio = f.v[i] / vavg if vavg > 0 else 0.0
    body = abs(f.c[i] - f.o[i])
    if d == 1:
        brk = f.c[i] > f.h[i - 24:i].max()
        ext = f.c[i] - f.ema20[i] <= 2.2 * a
        rsi_ok = f.rsi[i] <= 75
        dir_candle = f.c[i] > f.o[i]
    else:
        brk = f.c[i] < f.l[i - 24:i].min()
        ext = f.ema20[i] - f.c[i] <= 2.2 * a
        rsi_ok = f.rsi[i] >= 25
        dir_candle = f.c[i] < f.o[i]
    if brk and ext and rsi_ok and dir_candle and vol_ratio >= 1.5 and rng > 0 and body >= 0.5 * rng and f.adx[i] >= 18:
        return {"name": "breakout", "quality": 8 + min(12.0, (vol_ratio - 1.5) * 8 + 4),
                "note": f"24-bar range breakout on {vol_ratio:.1f}x volume"}
    return None


def _momentum(f: Frame, i: int, d: int) -> dict | None:
    """Institutional Impulse / Pump & Dump Catcher: EMA9/20/50 + VWAP alignment + MACD expansion + RSI + volume."""
    if i < 60:
        return None
    a = f.atr[i]
    rng = f.h[i] - f.l[i]
    if rng <= 0 or a <= 0:
        return None
    vavg = f.v[i - 20:i].mean()
    vol_ratio = f.v[i] / vavg if vavg > 0 else 1.0
    vol3_ratio = f.v[i - 2:i + 1].mean() / vavg if vavg > 0 else 1.0
    e9 = f.ema9[i] if f.ema9 is not None else f.ema20[i]
    vwap = f.vwap24[i] if f.vwap24 is not None else f.ema20[i]
    m_hist = f.macd_hist[i] if f.macd_hist is not None else 0.0
    m_prev = f.macd_hist[i - 1] if f.macd_hist is not None else 0.0
    obv_m = f.obv_mom[i] if f.obv_mom is not None else 0.0
    body = abs(f.c[i] - f.o[i])

    if d == 1:
        ribbon = f.c[i] > e9 > f.ema20[i] > f.ema50[i] and f.c[i] > vwap
        macd_ok = m_hist > 0 and m_hist >= m_prev
        rsi_ok = 54 <= f.rsi[i] <= 74 and f.rsi[i] > f.rsi[i - 2]
        vol_ok = (vol_ratio >= 1.25 or vol3_ratio >= 1.20) and (f.cmf[i] >= 0.03 or obv_m >= 0.18)
        candle_ok = f.c[i] > f.o[i] and body >= 0.45 * rng and (f.h[i] - f.c[i]) <= 0.32 * rng
        not_ext = (f.c[i] - f.ema20[i]) <= 2.0 * a
        brk_recent = f.c[i] > f.h[i - 8:i].max()
    else:
        ribbon = f.c[i] < e9 < f.ema20[i] < f.ema50[i] and f.c[i] < vwap
        macd_ok = m_hist < 0 and m_hist <= m_prev
        rsi_ok = 26 <= f.rsi[i] <= 46 and f.rsi[i] < f.rsi[i - 2]
        vol_ok = (vol_ratio >= 1.25 or vol3_ratio >= 1.20) and (f.cmf[i] <= -0.03 or obv_m <= -0.18)
        candle_ok = f.c[i] < f.o[i] and body >= 0.45 * rng and (f.c[i] - f.l[i]) <= 0.32 * rng
        not_ext = (f.ema20[i] - f.c[i]) <= 2.0 * a
        brk_recent = f.c[i] < f.l[i - 8:i].min()

    if ribbon and macd_ok and rsi_ok and vol_ok and candle_ok and not_ext and brk_recent:
        q = 14.0 + min(5.0, max(vol_ratio, vol3_ratio) * 2.5)
        return {
            "name": "momentum",
            "quality": round(q, 1),
            "note": f"institutional 1H momentum surge ({max(vol_ratio, vol3_ratio):.1f}x vol, RSI {f.rsi[i]:.0f}, VWAP+MACD aligned)",
        }
    return None


def _whale_reversal(f: Frame, i: int, d: int) -> dict | None:
    """RSI Divergence / Extreme Reversal + Whale Volume Absorption (optional, off by default)."""
    if i < 60:
        return None
    a = f.atr[i]
    vavg = f.v[i - 20:i].mean()
    vol_peak = max(f.v[i], f.v[i - 1]) / vavg if vavg > 0 else 1.0
    m_hist = f.macd_hist[i] if f.macd_hist is not None else 0.0
    m_prev = f.macd_hist[i - 1] if f.macd_hist is not None else 0.0

    prev_slice = slice(i - 22, i - 3)
    rec_slice = slice(i - 3, i + 1)
    if d == 1:
        prev_low_idx = (i - 22) + int(np.argmin(f.l[prev_slice]))
        rec_low_idx = (i - 3) + int(np.argmin(f.l[rec_slice]))
        price_lower_or_double = f.l[rec_low_idx] <= f.l[prev_low_idx] + 0.30 * a
        rsi_higher = f.rsi[rec_low_idx] >= f.rsi[prev_low_idx] + 3.0 and f.rsi[prev_low_idx] <= 38
        reclaim = f.c[i] > f.o[i] and f.c[i] > f.h[i - 1] and m_hist >= m_prev and f.cmf[i] > 0
        if price_lower_or_double and rsi_higher and reclaim and vol_peak >= 1.40:
            return {
                "name": "whale_reversal",
                "quality": 16.0,
                "note": f"RSI bullish divergence on {vol_peak:.1f}x whale volume (1H RSI {f.rsi[i]:.0f})",
            }
    else:
        prev_high_idx = (i - 22) + int(np.argmax(f.h[prev_slice]))
        rec_high_idx = (i - 3) + int(np.argmax(f.h[rec_slice]))
        price_higher_or_double = f.h[rec_high_idx] >= f.h[prev_high_idx] - 0.30 * a
        rsi_lower = f.rsi[rec_high_idx] <= f.rsi[prev_high_idx] - 3.0 and f.rsi[prev_high_idx] >= 62
        reject = f.c[i] < f.o[i] and f.c[i] < f.l[i - 1] and m_hist <= m_prev and f.cmf[i] < 0
        if price_higher_or_double and rsi_lower and reject and vol_peak >= 1.40:
            return {
                "name": "whale_reversal",
                "quality": 16.0,
                "note": f"RSI bearish divergence on {vol_peak:.1f}x whale volume (1H RSI {f.rsi[i]:.0f})",
            }
    return None


def _smc_sweep(f: Frame, i: int, d: int) -> dict | None:
    """Trend-Aligned Smart Money Liquidity Sweep (Stop-Hunt): sweeps N-bar swing pool and snaps back with trend."""
    if i < 60:
        return None
    a = f.atr[i]
    rng = f.h[i] - f.l[i]
    if rng <= 0 or a <= 0:
        return None
    vavg = f.v[i - 20:i].mean()
    vol_ratio = max(f.v[i], f.v[i - 1]) / vavg if vavg > 0 else 1.0
    pool_slice = slice(i - 24, i - 1)
    if d == 1:
        swing_low = f.l[pool_slice].min()
        swept = min(f.l[i], f.l[i - 1]) < swing_low - 0.05 * a and min(f.l[i], f.l[i - 1]) >= swing_low - 1.1 * a
        reclaimed = f.c[i] > swing_low and f.c[i] > f.o[i] and (f.c[i] - f.l[i]) >= 0.58 * rng and f.c[i] >= f.ema50[i] * 0.99
        rsi_ok = 35 <= f.rsi[i] <= 64
        if swept and reclaimed and rsi_ok and vol_ratio >= 1.25:
            return {
                "name": "smc_sweep",
                "quality": 15.0,
                "note": f"SMC liquidity sweep below {swing_low:.6g} reclaimed on {vol_ratio:.1f}x volume",
            }
    else:
        swing_high = f.h[pool_slice].max()
        swept = max(f.h[i], f.h[i - 1]) > swing_high + 0.05 * a and max(f.h[i], f.h[i - 1]) <= swing_high + 1.1 * a
        reclaimed = f.c[i] < swing_high and f.c[i] < f.o[i] and (f.h[i] - f.c[i]) >= 0.58 * rng and f.c[i] <= f.ema50[i] * 1.01
        rsi_ok = 36 <= f.rsi[i] <= 65
        if swept and reclaimed and rsi_ok and vol_ratio >= 1.25:
            return {
                "name": "smc_sweep",
                "quality": 15.0,
                "note": f"SMC liquidity sweep above {swing_high:.6g} rejected on {vol_ratio:.1f}x volume",
            }
    return None


_SETUP_FNS = {
    "pullback": _pullback,
    "breakout": _breakout,
    "momentum": _momentum,
    "smc_sweep": _smc_sweep,
    "whale_reversal": _whale_reversal,
}


# --------------------------------------------------------------------------
# Live Rolling Strategy Backtester (Causal Walk-Forward on Recent Candles)
# --------------------------------------------------------------------------

def backtest_setup_on_history(
    sf: Frame,
    si: int,
    direction: int,
    setup_name: str,
    cfg: dict | None = None,
) -> dict[str, Any]:
    """Causal walk-forward mini-backtest of ``setup_name`` on historical bars before ``si``.

    Evaluates how ``setup_name`` performed on recent market candles (up to ``si - 1``;
    strictly zero look-ahead) using the exact stop-loss, TP1/TP2/TP3, and breakeven rules.
    Returns win rate, SL-hit rate, average R, and whether the strategy passes the live gate.
    """
    cfg = cfg or default_config()
    fn = _SETUP_FNS.get(setup_name)
    if not fn or si < 90:
        return {"trades": 0, "wins": 0, "sls": 0, "win_rate": 100.0, "sl_rate": 0.0, "avg_r": 0.0, "passed": True, "summary": "BT warm-up"}

    lookback = int(cfg.get("bt_lookback_bars", 220))
    start_b = max(65, si - lookback)
    end_b = si - 6  # only evaluate historical signals that had at least 6 bars to play out before bar si
    swing_n = int(cfg.get("swing_bars", 12))
    tp1_r, tp2_r, tp3_r = float(cfg.get("tp1_r", 1.0)), float(cfg.get("tp2_r", 2.0)), float(cfg.get("tp3_r", 3.0))
    min_sl_pct = float(cfg.get("min_sl_pct", 0.85)) / 100.0
    max_sl_key = "pullback_max_sl_pct" if setup_name == "pullback" else "max_sl_pct"
    max_sl_pct = float(cfg.get(max_sl_key, cfg.get("max_sl_pct", 3.25))) / 100.0

    rs: list[float] = []
    sls = 0
    wins = 0
    b = start_b
    while b < end_b:
        # Check historical trend state at bar b (either matching direction or general trend)
        t_b = trend_state(sf, b)
        if t_b == 0:
            t_b = fast_impulse_direction(sf, b)
        if t_b not in (1, -1):
            b += 1
            continue
        s = fn(sf, b, t_b)
        if not s:
            b += 1
            continue
        entry = float(sf.c[b])
        a = float(sf.atr[b])
        if entry <= 0 or a <= 0:
            b += 1
            continue
        lo_s = float(sf.l[max(0, b - swing_n + 1):b + 1].min())
        hi_s = float(sf.h[max(0, b - swing_n + 1):b + 1].max())
        struct = (entry - (lo_s - 0.3 * a)) if t_b == 1 else ((hi_s + 0.3 * a) - entry)
        risk = max(struct, 1.8 * a, entry * min_sl_pct)
        if risk / entry > max_sl_pct:
            b += 1
            continue

        sl = entry - t_b * risk
        tp1 = entry + t_b * risk * tp1_r
        tp2 = entry + t_b * risk * tp2_r
        tp3 = entry + t_b * risk * tp3_r

        # Walk forward from b+1 up to min(b + 36, si) — never past si!
        rem = 1.0
        r_tot = 0.0
        hit1 = hit2 = False
        cur_sl = sl
        closed_at = min(b + 36, si)
        status = "TIME"
        for k in range(b + 1, closed_at + 1):
            lo_k, hi_k = float(sf.l[k]), float(sf.h[k])
            stop_hit = (lo_k <= cur_sl) if t_b == 1 else (hi_k >= cur_sl)
            if stop_hit:
                exit_r = (cur_sl - entry) * t_b / risk
                r_tot += rem * exit_r
                rem = 0.0
                status = "BE" if hit1 else "SL"
                closed_at = k
                break
            if not hit1 and ((hi_k >= tp1) if t_b == 1 else (lo_k <= tp1)):
                r_tot += (1.0 / 3.0) * tp1_r
                rem -= 1.0 / 3.0
                hit1 = True
                cur_sl = entry + t_b * (0.15 * risk)  # breakeven + 0.15R buffer
            if not hit2 and ((hi_k >= tp2) if t_b == 1 else (lo_k <= tp2)):
                r_tot += (1.0 / 3.0) * tp2_r
                rem -= 1.0 / 3.0
                hit2 = True
            if (hi_k >= tp3) if t_b == 1 else (lo_k <= tp3):
                r_tot += rem * tp3_r
                rem = 0.0
                status = "TP3"
                closed_at = k
                break
        if rem > 0:
            r_tot += rem * ((float(sf.c[closed_at]) - entry) * t_b / risk)
        r_net = r_tot - 0.08  # fee/slippage deduction in R
        rs.append(r_net)
        if r_net > 0:
            wins += 1
        if status == "SL":
            sls += 1
        b = max(b + 3, closed_at)  # prevent overlapping identical trades

    n = len(rs)
    if n == 0:
        return {"trades": 0, "wins": 0, "sls": 0, "win_rate": 100.0, "sl_rate": 0.0, "avg_r": 0.0, "passed": True,
                "summary": f"BT {setup_name}: clean regime (0 prior SLs)"}

    win_rate = round(wins / n * 100.0, 1)
    sl_rate = round(sls / n * 100.0, 1)
    avg_r = round(float(np.mean(rs)), 2)
    min_trades = int(cfg.get("bt_min_trades", 2))
    max_sl_rate = float(cfg.get("bt_max_sl_pct", 55.0))
    min_win_rate = float(cfg.get("bt_min_win_pct", 45.0))

    passed = True
    if n >= min_trades:
        if sl_rate > max_sl_rate or (win_rate < min_win_rate and avg_r < 0.0) or avg_r < -0.15:
            passed = False

    return {
        "trades": n,
        "wins": wins,
        "sls": sls,
        "win_rate": win_rate,
        "sl_rate": sl_rate,
        "avg_r": avg_r,
        "passed": passed,
        "summary": f"Live BT ({n} trades): {win_rate:.0f}% win, {sl_rate:.0f}% SL, {avg_r:+.2f}R avg",
    }


def _no_setup_desk_note(sf: Frame, si: int, direction: int, tf4: bool) -> str:
    """Return a clean, professional institutional desk diagnostic when no entry setup fired on this bar."""
    tf_label = "4H" if tf4 else "1H"
    bias = "Bullish" if direction == 1 else "Bearish"
    c = float(sf.c[si])
    o = float(sf.o[si])
    e20 = float(sf.ema20[si])
    a = max(float(sf.atr[si]), 1e-9)
    r = float(sf.rsi[si])
    dist_pct = (c - e20) / e20 * 100 if e20 > 0 else 0.0
    dist_atr = abs(c - e20) / a
    hi24 = float(sf.h[max(0, si - 24):si].max()) if si >= 2 else c
    lo24 = float(sf.l[max(0, si - 24):si].min()) if si >= 2 else c
    vavg = float(sf.v[max(0, si - 20):si].mean()) if si >= 5 else float(sf.v[si])
    vol_r = float(sf.v[si]) / vavg if vavg > 0 else 1.0

    candle_aligned = (c >= o and r >= 48) if direction == 1 else (c <= o and r <= 52)
    if dist_atr <= 0.45 and candle_aligned:
        return f"{bias} {tf_label} setup forming at EMA20 ({dist_pct:+.1f}%, RSI {r:.0f}, vol {vol_r:.1f}x) — awaiting breakout/reclaim"
    if direction == 1 and hi24 > 0 and (hi24 - c) / a <= 0.55:
        return f"Bullish {tf_label} breakout setup near 24h high (RSI {r:.0f}, vol {vol_r:.1f}x) — needs volume push"
    if direction == -1 and lo24 > 0 and (c - lo24) / a <= 0.55:
        return f"Bearish {tf_label} breakdown setup near 24h low (RSI {r:.0f}, vol {vol_r:.1f}x) — needs sell volume"
    if r <= 33 or r >= 67:
        return f"{tf_label} RSI extreme ({r:.0f}, vol {vol_r:.1f}x) — watching for whale reversal setup"
    if direction == -1 and c > e20:
        return f"Bearish HTF bias · counter-bounce above 1H EMA20 ({dist_pct:+.1f}%, RSI {r:.0f}) — watching for rejection"
    if direction == 1 and c < e20:
        return f"Bullish HTF bias · pullback below 1H EMA20 ({dist_pct:+.1f}%, RSI {r:.0f}) — watching for reclaim"
    return f"{bias} bias (RSI {r:.0f}, EMA20 {dist_pct:+.1f}%, vol {vol_r:.1f}x) — mid-range consolidation"


# --------------------------------------------------------------------------
# Main evaluation
# --------------------------------------------------------------------------

def default_config() -> dict[str, Any]:
    return {
        "min_score": _env("ADMIRAL_MIN_SCORE", 72),
        "grade_a": _env("ADMIRAL_GRADE_A_SCORE", 76),
        "adx_min": _env("ADMIRAL_ADX_MIN", 18),
        "funding_crowded_pct": _env("ADMIRAL_FUNDING_CROWDED_PCT", 0.05),
        "stop_min_atr1h": _env("ADMIRAL_STOP_MIN_ATR1H", 1.8),
        "stop_min_atr4h": _env("ADMIRAL_STOP_MIN_ATR4H", 0.7),
        "stop_max_atr4h": _env("ADMIRAL_STOP_MAX_ATR4H", 2.6),
        "min_sl_pct": _env("ADMIRAL_MIN_SL_PCT", 0.85),
        "max_sl_pct": _env("ADMIRAL_MAX_SL_PCT", 3.25),
        "pullback_max_sl_pct": _env("ADMIRAL_PULLBACK_MAX_SL_PCT", 2.65),
        "swing_bars": _env("ADMIRAL_SWING_BARS", 12),
        "tp1_r": _env("ADMIRAL_TP1_R", 1.0),
        "tp2_r": _env("ADMIRAL_TP2_R", 2.0),
        "tp3_r": _env("ADMIRAL_TP3_R", 3.0),
        "min_room_r": _env("ADMIRAL_MIN_ROOM_R", 1.15),
        "max_chase_atr1h": _env("ADMIRAL_MAX_CHASE_ATR1H", 1.2),
        "macro_penalty": _env("ADMIRAL_MACRO_PENALTY", 8),
        "setup_tf": _env("ADMIRAL_SETUP_TF", 1),
        "setups": os.getenv("ADMIRAL_SETUPS", "pullback,breakout,momentum,smc_sweep"),
        "live_backtest": _env("ADMIRAL_LIVE_BACKTEST", 1),
        "bt_lookback_bars": _env("ADMIRAL_BT_LOOKBACK_BARS", 220),
        "bt_min_trades": _env("ADMIRAL_BT_MIN_TRADES", 2),
        "bt_max_sl_pct": _env("ADMIRAL_BT_MAX_SL_PCT", 55.0),
        "bt_min_win_pct": _env("ADMIRAL_BT_MIN_WIN_PCT", 45.0),
        "swing_bars_4h": _env("ADMIRAL_SWING_BARS_4H", 6),
        "stop4_min_atr": _env("ADMIRAL_STOP4_MIN_ATR", 1.2),
        "stop4_max_atr": _env("ADMIRAL_STOP4_MAX_ATR", 3.0),
    }


def flow_alignment(flow: dict | None, direction: int) -> tuple[float | None, list[str]]:
    """-1..+1 agreement of institutional/whale orderflow proxies with the trade direction."""
    if not flow:
        return None, []
    parts, weights, notes = [], [], []
    r1 = flow.get("taker_buy_ratio_1h")
    r4 = flow.get("taker_buy_ratio_4h")
    r24 = flow.get("taker_buy_ratio_24h")
    cb = flow.get("coinbase_premium_pct")
    etf = flow.get("etf_flow_score")
    ob_imb = flow.get("orderbook_imbalance")
    ls_ratio = flow.get("retail_ls_ratio")

    if r4 is not None:
        parts.append(_clip((r4 - 0.5) * direction / 0.04, -1, 1)); weights.append(0.30)
        notes.append(f"taker buy share 4h {r4 * 100:.0f}%")
    if r24 is not None:
        parts.append(_clip((r24 - 0.5) * direction / 0.03, -1, 1)); weights.append(0.25)
        notes.append(f"24h {r24 * 100:.0f}%")
    if cb is not None:
        parts.append(_clip(cb * direction / 0.15, -1, 1)); weights.append(0.25)
        notes.append(f"Coinbase premium {cb:+.2f}%")
    if etf is not None:
        parts.append(_clip(etf * direction, -1, 1)); weights.append(0.20)
        notes.append(f"ETF 7d flow {'inflow' if etf > 0.05 else 'outflow' if etf < -0.05 else 'flat'}")
    if r1 is not None:
        parts.append(_clip((r1 - 0.5) * direction / 0.05, -1, 1)); weights.append(0.20)
        notes.append(f"1h whale taker {r1 * 100:.0f}%")
    if ob_imb is not None and isinstance(ob_imb, (int, float)):
        parts.append(_clip(float(ob_imb) * direction / 0.25, -1, 1)); weights.append(0.15)
        notes.append(f"book depth {float(ob_imb) * 100:+.0f}%")
    if ls_ratio is not None and isinstance(ls_ratio, (int, float)):
        if direction == 1 and ls_ratio < 0.90:
            parts.append(0.8); weights.append(0.10)
            notes.append(f"retail short-crowded (L/S {ls_ratio:.2f})")
        elif direction == -1 and ls_ratio > 1.85:
            parts.append(0.8); weights.append(0.10)
            notes.append(f"retail long-crowded (L/S {ls_ratio:.2f})")
    if not parts:
        return None, []
    return sum(p * w for p, w in zip(parts, weights)) / sum(weights), notes


def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _pick_setup(sf: Frame, si: int, direction: int, allowed: str, cfg: dict | None = None, run_bt: bool = False) -> tuple[dict | None, str | None]:
    """Evaluate all enabled setups in ``allowed``, run live rolling backtest if enabled, and return the best passing setup."""
    candidates: list[dict] = []
    for name in ("pullback", "breakout", "momentum", "smc_sweep", "whale_reversal"):
        if name in allowed:
            s = _SETUP_FNS[name](sf, si, direction)
            if s:
                candidates.append(s)
    if not candidates:
        return None, None
    if not run_bt:
        return max(candidates, key=lambda item: item["quality"]), None

    passing: list[dict] = []
    rejected_notes: list[str] = []
    for cand in candidates:
        bt_res = backtest_setup_on_history(sf, si, direction, cand["name"], cfg=cfg)
        cand["bt"] = bt_res
        if bt_res["passed"]:
            # Rank by base quality + proven rolling backtest expectancy
            cand["effective_quality"] = cand["quality"] + max(-3.0, min(5.0, bt_res["avg_r"] * 4.0))
            passing.append(cand)
        else:
            rejected_notes.append(
                f"Live Backtest rejected {cand['name']} ({bt_res['win_rate']:.0f}% win, {bt_res['sl_rate']:.0f}% SL, {bt_res['avg_r']:+.2f}R on recent candles)"
            )
    if not passing:
        return None, "; ".join(rejected_notes[:2])
    best = max(passing, key=lambda item: item.get("effective_quality", item["quality"]))
    return best, None


def evaluate(f1: Frame, i1: int, f4: Frame, i4: int, fd: Frame, idd: int, *, price: float | None = None,
             ticker: str = "", btc_trend_4h: int | None = None, funding_pct: float | None = None,
             oi_change_pct: float | None = None, flow: dict | None = None, macro: dict | None = None,
             cfg: dict | None = None) -> dict[str, Any]:
    """Return the trade decision for the last CLOSED 1H bar ``i1``."""
    cfg = cfg or default_config()
    tf4 = int(cfg.get("setup_tf", 1)) == 4
    allowed = str(cfg.get("setups", "pullback,breakout,momentum,smc_sweep"))
    v4_agile = any(k in allowed for k in ("momentum", "smc_sweep", "whale_reversal")) and not tf4
    run_bt = bool(int(cfg.get("live_backtest", 1))) and v4_agile
    price = float(price if price else f1.c[i1])
    is_btc = ticker.upper().startswith("BTC")
    blockers: list[str] = []
    regime = regime_summary(f1, i1, f4, i4, fd, idd)
    out: dict[str, Any] = {"action": "NO TRADE", "score": 0.0, "grade": "", "setup": "", "levels": None,
                           "blockers": blockers, "reasons": [], "regime": regime, "components": {},
                           "live_backtest": None,
                           "coverage": 1.0 if (funding_pct is not None and oi_change_pct is not None) else
                           0.9 if (funding_pct is not None or oi_change_pct is not None) else 0.8}

    if i1 < 60 or i4 < 60 or idd < 55:
        blockers.append("not enough candle history yet")
        return out

    d1, d4, d1d = trend_state(f1, i1), trend_state(f4, i4), trend_state(fd, idd)
    imp1 = fast_impulse_direction(f1, i1)
    adx4 = float(f4.adx[i4])
    adx1 = float(f1.adx[i1])

    if tf4:
        sf, si = f4, i4
        if d1d == 0:
            blockers.append("1D trend is mixed - no directional edge")
            return out
        direction = d1d
        side = "BUY" if direction == 1 else "SELL"
        if d4 != direction:
            blockers.append(f"4H trend does not agree with the 1D {side} bias")
        need_adx = cfg["adx_min"]
        since_close_ms = (f1.ts[i1] + 3_600_000) - (f4.ts[i4] + 4 * 3_600_000)
        if since_close_ms > 3_600_000:
            blockers.append("waiting for the next 4H candle close")
        if adx4 < need_adx:
            blockers.append(f"4H trend too weak (ADX {adx4:.0f} < {need_adx:.0f})")
    elif not v4_agile:
        sf, si = f1, i1
        if d4 == 0:
            blockers.append(f"4H trend is mixed (ADX {adx4:.0f}) - no directional edge")
            return out
        direction = d4
        side = "BUY" if direction == 1 else "SELL"
        if d1d == -direction:
            blockers.append(f"1D trend opposes the {side}")
        need_adx = cfg["adx_min"] + (5 if d1d == 0 else 0)
        if adx4 < need_adx:
            blockers.append(f"4H trend too weak (ADX {adx4:.0f} < {need_adx:.0f})")
    else:
        # v4 Institutional Mode: never catch falling knives against 4H/1D trend!
        sf, si = f1, i1
        if d4 != 0:
            direction = d4
        elif d1d != 0 and (d1 == d1d or imp1 == d1d):
            direction = d1d
        elif d1 != 0 and d1d == 0 and adx1 >= 22:
            direction = d1
        else:
            blockers.append(f"Range / mixed HTF structure (4H ADX {adx4:.0f}, 1H RSI {f1.rsi[i1]:.0f}) — waiting for trend alignment")
            return out
        side = "BUY" if direction == 1 else "SELL"
        # Hard guard from 38-trade post-mortem: never fight an opposing 1D trend unless both 4H & 1H strongly align with ADX >= 28
        if d1d == -direction and not (d4 == direction and d1 == direction and adx4 >= 28):
            blockers.append(f"1D trend ({_LABEL[d1d].lower()}) opposes {side} — counter-trend blocked")
            return out
        need_adx = cfg["adx_min"]
        if adx4 < need_adx and adx1 < (need_adx + 4):
            blockers.append(f"Low trend momentum (4H ADX {adx4:.0f}, 1H ADX {adx1:.0f} < {need_adx:.0f})")

    if not is_btc and btc_trend_4h is not None and btc_trend_4h == -direction:
        if not v4_agile or not (d4 == direction and d1 == direction and adx4 >= 25):
            blockers.append(f"BTC 4H trend ({_LABEL[btc_trend_4h].lower()}) opposes {side}")
    if funding_pct is not None:
        crowd = cfg["funding_crowded_pct"]
        if (direction == 1 and funding_pct > crowd) or (direction == -1 and funding_pct < -crowd):
            blockers.append(f"funding {funding_pct:+.3f}% shows a crowded {'long' if direction == 1 else 'short'} side")
    macro = macro or {}
    if macro.get("blackout"):
        blockers.append(f"macro event blackout: {macro.get('blackout_reason', 'high-impact event')}")
    flow_score, flow_notes = flow_alignment(flow, direction)
    if flow_score is not None and flow_score <= -0.5:
        blockers.append("institutional-flow proxies (taker CVD / Coinbase premium) oppose the " + side)
    atr4_pct = f4.atr[i4] / price * 100
    if atr4_pct > 6.0:
        blockers.append(f"4H volatility too high (ATR {atr4_pct:.1f}%)")

    setup, bt_reject_reason = _pick_setup(sf, si, direction, allowed, cfg=cfg, run_bt=run_bt)
    if setup and v4_agile:
        vavg_now = float(sf.v[max(0, si - 20):si].mean()) if si >= 5 else float(sf.v[si])
        vol_now = float(sf.v[si]) / vavg_now if vavg_now > 0 else 1.0
        rsi_now = float(sf.rsi[si])
        ob_imb = (flow or {}).get("orderbook_imbalance")
        if setup["name"] == "breakout":
            # Anti-climax exhaustion guard (prevents selling the bottom of a 5x-6x volume capitulation candle like BTC @ 73.2k)
            if vol_now >= 4.5 and ((direction == -1 and rsi_now <= 35.5) or (direction == 1 and rsi_now >= 64.5)):
                setup = None
                bt_reject_reason = f"1H climax exhaustion spike ({vol_now:.1f}x vol at RSI {rsi_now:.0f}) — waiting for pullback"
            elif isinstance(ob_imb, (int, float)) and float(ob_imb) * direction <= -0.18 and ((direction == -1 and rsi_now <= 37.0) or (direction == 1 and rsi_now >= 63.0)):
                setup = None
                bt_reject_reason = f"Orderbook absorption ({float(ob_imb)*100:+.0f}% depth) opposes exhausted breakout (RSI {rsi_now:.0f})"
        elif setup["name"] == "pullback" and sf.macd_hist is not None and si >= 2:
            # Anti-relief-bounce guard (prevents shorting a weak pullback candle while 1H MACD momentum is surging against the trade like LTC)
            macd_delta = (float(sf.macd_hist[si]) - float(sf.macd_hist[si - 1])) * direction
            if macd_delta < -0.12 * float(sf.atr[si]) and ((direction == -1 and rsi_now >= 53.0) or (direction == 1 and rsi_now <= 47.0)):
                setup = None
                bt_reject_reason = f"1H counter-bounce momentum still expanding (RSI {rsi_now:.0f}) — awaiting clean rejection"
    if not setup:
        if bt_reject_reason:
            blockers.append(bt_reject_reason)
        else:
            blockers.append(_no_setup_desk_note(sf, si, direction, tf4) if v4_agile else
                            f"no {'4H' if tf4 else '1H'} entry setup (needs EMA20 pullback-reclaim or volume breakout)")
    if abs(price - f1.c[i1]) > cfg["max_chase_atr1h"] * f1.atr[i1]:
        blockers.append("price already ran away from the signal candle (would be chasing)")
    if blockers:
        return out

    # ---- levels & Stop-Loss Sweet-Spot Guard (0.85% .. 2.55% of price) -----
    a1, a4 = float(f1.atr[i1]), float(f4.atr[i4])
    if tf4:
        n = int(cfg.get("swing_bars_4h", 6))
        a_s = a4
        lo_s, hi_s = f4.l[i4 - n + 1:i4 + 1].min(), f4.h[i4 - n + 1:i4 + 1].max()
        min_dist, max_dist = cfg["stop4_min_atr"] * a4, cfg["stop4_max_atr"] * a4
    else:
        n = int(cfg["swing_bars"])
        a_s = a1
        lo_s, hi_s = f1.l[i1 - n + 1:i1 + 1].min(), f1.h[i1 - n + 1:i1 + 1].max()
        min_dist = max(cfg["stop_min_atr1h"] * a1, cfg["stop_min_atr4h"] * a4)
        max_dist = cfg["stop_max_atr4h"] * a4
        if v4_agile:
            min_sl_pct = float(cfg.get("min_sl_pct", 0.85)) / 100.0
            if setup["name"] == "pullback":
                max_sl_pct = float(cfg.get("pullback_max_sl_pct", cfg.get("max_sl_pct", 2.65))) / 100.0
            else:
                max_sl_pct = float(cfg.get("max_sl_pct", 3.25)) / 100.0
            min_dist = max(min_dist, price * min_sl_pct)
            max_dist = min(max_dist, price * max_sl_pct)

    structure = (price - (lo_s - 0.3 * a_s)) if direction == 1 else ((hi_s + 0.3 * a_s) - price)
    risk = max(structure, min_dist)
    if risk > max_dist:
        blockers.append(f"structure stop too wide ({risk / price * 100:.1f}% > {max_dist / price * 100:.1f}% max SL limit)")
        return out
    sl = price - direction * risk
    tps = [price + direction * risk * cfg[k] for k in ("tp1_r", "tp2_r", "tp3_r")]
    wf, wi, span = (fd, idd, 30) if tf4 else (f4, i4, 30)
    lookback = slice(max(0, wi - span), wi + 1)
    if direction == 1:
        wall = wf.h[lookback].max()
        room = (wall - price) / risk if wall > price else float("inf")
    else:
        wall = wf.l[lookback].min()
        room = (price - wall) / risk if wall < price else float("inf")
    if room < cfg["min_room_r"] and setup["name"] != "breakout":
        blockers.append(f"only {room:.1f}R of room before the nearest {'1D' if tf4 else '4H'} level {wall:.6g}")
        return out

    # ---- score (0-100) -------------------------------------------------------
    comp = {}
    if tf4:
        comp["htf_alignment"] = 15 + (10 if d4 == direction else 0)
    else:
        htf = 15 if d4 == direction else 10
        htf += 10 if d1d == direction else (4 if d1d == 0 else 0)
        comp["htf_alignment"] = float(htf)
    eff_adx = max(adx4, adx1 * 0.85) if v4_agile else adx4
    comp["trend_strength"] = _clip((eff_adx - 15) / 20, 0, 1) * 15
    comp["setup"] = float(setup["quality"])
    c_flow = float(f4.cmf[i4])
    if v4_agile and f1.obv_mom is not None:
        c_flow = 0.65 * c_flow + 0.35 * (float(f1.obv_mom[i1]) * 0.15)
    comp["flow_cmf"] = 5 + 5 * _clip(c_flow * direction / 0.15, -1, 1)
    r1 = float(sf.rsi[si])
    comp["rsi_room"] = 5 * _clip(((70 - r1) if direction == 1 else (r1 - 30)) / 25, 0, 1)
    deriv = 0.0
    deriv += 2.5 if funding_pct is None else (5 if abs(funding_pct) <= 0.02 or funding_pct * direction < 0 else 2.5)
    if oi_change_pct is None:
        deriv += 2.5
    else:
        deriv += 5 if (oi_change_pct > 0.3) else 2.5 if oi_change_pct > -0.5 else 0
    comp["derivatives"] = deriv
    if is_btc:
        comp["market_alignment"] = 5 if d1d == direction else (4 if d1 == direction else 2)
    else:
        comp["market_alignment"] = 5 if btc_trend_4h == direction else 2.5
    comp["institutional_flow"] = 7.5 if flow_score is None else 7.5 + 7.5 * flow_score
    raw = sum(comp.values())
    score = raw / 105.0 * 100.0
    macro_adj = 0.0
    if macro.get("risk") in (-1, 1) and macro["risk"] == -direction:
        macro_adj = -float(cfg.get("macro_penalty", 8.0))
        score += macro_adj
    elif v4_agile and macro.get("news_sentiment") is not None:
        ns = float(macro.get("news_sentiment") or 0.0) * direction
        if ns >= 0.20:
            macro_adj = round(min(5.0, ns * 6.0), 1)
            score = min(100.0, score + macro_adj)
    comp["macro_adjustment"] = macro_adj

    bt_info = setup.get("bt")
    if bt_info and bt_info.get("trades", 0) >= 2:
        bt_bonus = round(_clip(bt_info["avg_r"] * 4.0, -4.0, 5.0), 1)
        comp["live_backtest_bonus"] = bt_bonus
        score = _clip(score + bt_bonus, 0.0, 100.0)
        out["live_backtest"] = bt_info

    out["components"] = {k: round(v, 1) for k, v in comp.items()}
    out["score"] = round(score, 1)
    if score < cfg["min_score"]:
        blockers.append(f"setup quality {score:.0f}/100 is below the {cfg['min_score']:.0f} minimum")
        return out

    reasons = [
        f"4H {_LABEL[d4].lower()} (ADX {adx4:.0f})",
        f"1D {_LABEL[d1d].lower()}",
        setup["note"],
        f"4H CMF {float(f4.cmf[i4]):+.2f}",
    ]
    if bt_info and bt_info.get("summary"):
        reasons.append(bt_info["summary"])
    if funding_pct is not None:
        reasons.append(f"funding {funding_pct:+.3f}%")
    if oi_change_pct is not None:
        reasons.append(f"OI {oi_change_pct:+.1f}% (4h)")
    if not is_btc and btc_trend_4h is not None:
        reasons.append(f"BTC 4H {_LABEL[btc_trend_4h].lower()}")
    reasons.extend(flow_notes)
    if macro.get("catalysts"):
        reasons.append("global news (" + ", ".join(macro["catalysts"][:2]) + ")")
    elif macro_adj < 0:
        reasons.append("macro headwind (" + "; ".join(macro.get("notes", [])[:2]) + ")")
    out["flow_score"] = flow_score
    out.update({
        "action": side, "grade": "A" if score >= cfg["grade_a"] else "B", "setup": setup["name"],
        "reasons": reasons,
        "levels": {"entry": price, "sl": sl, "tp1": tps[0], "tp2": tps[1], "tp3": tps[2], "risk": risk,
                   "risk_pct": risk / price * 100, "room_r": None if room == float("inf") else round(room, 2),
                   "rr": round(sum(cfg[k] for k in ("tp1_r", "tp2_r", "tp3_r")) / 3, 2),
                   "basis": f"{'4H' if tf4 else '1H'} {n}-bar swing stop (SL {risk / price * 100:.1f}% of price), "
                            f"targets {cfg['tp1_r']:g}R/{cfg['tp2_r']:g}R/{cfg['tp3_r']:g}R"},
    })
    return out


def evaluate_live(c1: dict, c4: dict, cd: dict, **kwargs) -> dict[str, Any]:
    """Convenience wrapper: build frames from fetched closed candles and evaluate the last bar."""
    f1, f4, fd = build_frame(c1), build_frame(c4), build_frame(cd)
    return evaluate(f1, len(f1) - 1, f4, len(f4) - 1, fd, len(fd) - 1, **kwargs)


def btc_trend_from_candles(c4: dict) -> int:
    f4 = build_frame(c4)
    return trend_state(f4, len(f4) - 1)

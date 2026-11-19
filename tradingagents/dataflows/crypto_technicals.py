"""
Technical indicators (RSI, MACD, Bollinger Bands) computed directly from
verified OKX candles with pandas — no ta-lib/pandas-ta/freqtrade
dependency needed, just the standard textbook formulas.
"""
import requests
import pandas as pd



def _binance_symbol(ticker: str) -> str:
    base = ticker.split("-")[0].replace("USDT", "").replace("USD", "").upper()
    return f"{base}USDT"


_OKX_BARS = {"1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m", "1h": "1H", "4h": "4H", "1d": "1D"}


def _okx_closes(ticker: str, interval: str, limit: int = 100) -> "pd.Series | None":
    """Fallback close-price series via OKX public candles when Binance is
    unavailable/geo-blocked, so RSI/MACD/Bollinger don't just go silent."""
    bar = _OKX_BARS.get(interval)
    if not bar:
        return None
    base = ticker.split("-")[0].replace("USDT", "").replace("USD", "").upper()
    inst_id = f"{base}-USDT-SWAP"
    try:
        url = "https://www.okx.com/api/v5/market/candles"
        params = {"instId": inst_id, "bar": bar, "limit": min(limit, 300)}
        res = requests.get(url, params=params, timeout=10).json()
        rows = res.get("data") or []
        if len(rows) < 35:
            return None
        rows = list(reversed(rows))  # OKX returns newest-first
        return pd.Series([float(r[4]) for r in rows])
    except Exception:
        return None


def _rsi(closes: pd.Series, period: int = 14) -> float:
    delta = closes.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-10)
    rsi = 100 - (100 / (1 + rs))
    return float(rsi.iloc[-1])


def _macd(closes: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    ema_fast = closes.ewm(span=fast, adjust=False).mean()
    ema_slow = closes.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return float(macd_line.iloc[-1]), float(signal_line.iloc[-1]), float(histogram.iloc[-1])


def _bollinger(closes: pd.Series, period: int = 20, std_mult: float = 2.0):
    mid = closes.rolling(period).mean()
    std = closes.rolling(period).std()
    upper = mid + std_mult * std
    lower = mid - std_mult * std
    last_close = float(closes.iloc[-1])
    band_width = float(upper.iloc[-1] - lower.iloc[-1])
    # 0 = at lower band, 1 = at upper band, >1/<0 = price outside the bands
    pct_b = (last_close - lower.iloc[-1]) / band_width if band_width else 0.5
    return float(upper.iloc[-1]), float(mid.iloc[-1]), float(lower.iloc[-1]), float(pct_b)


def get_technical_indicators(ticker: str, interval: str = "1h") -> str:
    """
    Returns a short, LLM-readable summary of RSI(14), MACD(12,26,9), and
    Bollinger Bands(20,2) for the given ticker, computed from recent Binance
    klines. Returns an empty
    string on any failure (caller should treat that as "indicators
    unavailable" and just skip this context).
    """
    # OKX is the verified source for this runner; don't repeat known 451
    # Binance calls for every indicator on every coin.
    closes = _okx_closes(ticker, interval)

    if closes is None:
        return ""

    try:
        rsi = _rsi(closes)
        macd_line, signal_line, histogram = _macd(closes)
        upper, mid, lower, pct_b = _bollinger(closes)

        if rsi >= 70:
            rsi_read = "overbought"
        elif rsi <= 30:
            rsi_read = "oversold"
        else:
            rsi_read = "neutral"

        macd_read = "bullish (MACD above signal)" if histogram > 0 else "bearish (MACD below signal)"

        if pct_b > 1:
            bb_read = "price above the upper band (extended)"
        elif pct_b < 0:
            bb_read = "price below the lower band (extended)"
        elif pct_b > 0.8:
            bb_read = "near the upper band"
        elif pct_b < 0.2:
            bb_read = "near the lower band"
        else:
            bb_read = "inside the bands, near the middle"

        return (
            f"TECHNICAL INDICATORS ({interval} candles):\n"
            f"- RSI(14): {rsi:.1f} -- {rsi_read}\n"
            f"- MACD(12,26,9): line {macd_line:.4f}, signal {signal_line:.4f}, "
            f"histogram {histogram:.4f} -- {macd_read}\n"
            f"- Bollinger Bands(20,2): upper {upper:.4f}, mid {mid:.4f}, lower {lower:.4f} "
            f"-- {bb_read} (%B={pct_b:.2f})\n"
            "Weigh these against the order-flow and institutional reads above -- "
            "flag it if momentum (RSI/MACD) and positioning (funding/OI/CVD) disagree."
        )
    except Exception:
        return ""

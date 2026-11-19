"""OKX public market data & institutional/whale orderflow proxies (no API key, no LLM).

One small module owns every network call of the bot, so rate limits, retries
and caching live in a single place.  Only *closed* candles are returned by
``fetch_candles`` -- the forming candle is dropped so indicators are computed
on clean bars, while ``get_prices`` and orderflow endpoints give real-time
execution context.
"""
from __future__ import annotations

import os
import time
from typing import Any

import numpy as np
import requests

HOSTS = [h for h in (os.getenv("OKX_HOST", "").strip(), "https://www.okx.com", "https://aws.okx.com") if h]
_session = requests.Session()
_session.headers.update({"User-Agent": "future-admiral/3.0"})
_CACHE: dict[tuple, tuple[float, Any]] = {}
_CACHE_TTL = 45.0
_last_call = [0.0]
BARS = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "1H", "4h": "4H", "1d": "1D"}
BAR_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}


class MarketDataError(RuntimeError):
    pass


def inst_id(ticker: str, swap: bool = True) -> str:
    base = ticker.split("-")[0].upper()
    return f"{base}-USDT-SWAP" if swap else f"{base}-USDT"


def _get(path: str, params: dict, retries: int = 2, timeout: float = 10.0):
    """GET an OKX v5 endpoint, rotate hosts on failure, return ``data`` rows."""
    last = "unknown error"
    for attempt in range(retries + 1):
        for host in HOSTS:
            wait = 0.12 - (time.time() - _last_call[0])
            if wait > 0:
                time.sleep(wait)
            _last_call[0] = time.time()
            try:
                response = _session.get(f"{host}{path}", params=params, timeout=timeout)
                if response.status_code == 429:
                    last = "HTTP 429 rate limited"
                    time.sleep(1.5 * (attempt + 1))
                    continue
                response.raise_for_status()
                payload = response.json()
                if str(payload.get("code")) != "0":
                    # 51001 = instrument does not exist: deterministic, do not retry.
                    raise MarketDataError(f"OKX {payload.get('code')}: {payload.get('msg')}")
                return payload.get("data") or []
            except MarketDataError:
                raise
            except Exception as exc:  # network / JSON problems
                last = " ".join(str(exc).split())[:160]
        time.sleep(0.8 * (attempt + 1))
    raise MarketDataError(last)


def _cached(key: tuple, producer):
    now = time.time()
    hit = _CACHE.get(key)
    if hit and now - hit[0] < _CACHE_TTL:
        return hit[1]
    value = producer()
    _CACHE[key] = (now, value)
    return value


def clear_cache() -> None:
    _CACHE.clear()


def _rows_to_arrays(rows: list, closed_only: bool) -> dict[str, np.ndarray]:
    rows = list(reversed(rows))  # OKX is newest-first
    if closed_only:
        rows = [r for r in rows if len(r) < 9 or str(r[8]) == "1"]
    if not rows:
        raise MarketDataError("no candle rows")
    arr = np.array([[float(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])] for r in rows])
    return {"ts": arr[:, 0], "o": arr[:, 1], "h": arr[:, 2], "l": arr[:, 3], "c": arr[:, 4], "v": arr[:, 5]}


def fetch_candles(ticker: str, tf: str, limit: int = 300, closed_only: bool = True) -> dict[str, np.ndarray]:
    """Closed OHLCV arrays (oldest first) for ``tf`` in 1m/5m/15m/1h/4h/1d."""
    bar = BARS[tf]

    def produce():
        errors = []
        for swap in (True, False):
            try:
                rows = _get("/api/v5/market/candles", {"instId": inst_id(ticker, swap), "bar": bar, "limit": min(limit, 300)})
                if rows:
                    return rows
                errors.append("empty")
            except MarketDataError as exc:
                errors.append(str(exc))
        raise MarketDataError(f"{ticker} {tf} candles unavailable: {' | '.join(errors)[:200]}")

    rows = _cached(("candles", ticker, tf, limit), produce)
    return _rows_to_arrays(rows, closed_only)


def get_prices(tickers: list[str]) -> dict[str, float]:
    """Last traded SWAP price for every ticker in ONE request."""
    wanted = {inst_id(t): t for t in tickers}

    def produce():
        return _get("/api/v5/market/tickers", {"instType": "SWAP"})

    rows = _cached(("tickers",), produce)
    out = {}
    for row in rows:
        ticker = wanted.get(row.get("instId"))
        if ticker and row.get("last"):
            try:
                out[ticker] = float(row["last"])
            except (TypeError, ValueError):
                pass
    return out


def get_price(ticker: str) -> float | None:
    price = get_prices([ticker]).get(ticker)
    if price:
        return price
    try:  # spot fallback for coins without a perpetual swap
        rows = _get("/api/v5/market/ticker", {"instId": inst_id(ticker, swap=False)})
        return float(rows[0]["last"]) if rows else None
    except Exception:
        return None


def get_funding_pct(ticker: str) -> float | None:
    """Current funding rate in percent per 8h (e.g. 0.01 == 0.01%)."""
    try:
        rows = _cached(("funding", ticker), lambda: _get("/api/v5/public/funding-rate", {"instId": inst_id(ticker)}))
        return float(rows[0]["fundingRate"]) * 100 if rows else None
    except Exception:
        return None


def get_oi_change_pct(ticker: str, hours: int = 4) -> float | None:
    """Open-interest change over roughly the last ``hours`` hours, in percent."""
    try:
        rows = _cached(("oi", ticker, hours), lambda: _get(
            "/api/v5/rubik/stat/contracts/open-interest-history",
            {"instId": inst_id(ticker), "period": "1H", "limit": max(2, hours + 1)}))
        points = []
        for row in rows:
            ts = float(row[0]) if isinstance(row, (list, tuple)) else float(row.get("ts"))
            val = row[1] if isinstance(row, (list, tuple)) else (row.get("oiCcy") or row.get("oi"))
            points.append((ts, float(val)))
        if len(points) < 2:
            return None
        points.sort()
        return (points[-1][1] / points[0][1] - 1) * 100 if points[0][1] else None
    except Exception:
        return None


def get_range_since(ticker: str, since_ms: int) -> dict | None:
    """High/low traded since ``since_ms``, using the finest bar that covers the gap.

    Includes the still-forming candle so a stop touched a minute ago is seen.
    """
    gap = max(0.0, time.time() - since_ms / 1000)
    plan = [("1m", 290 * 60), ("5m", 290 * 300), ("15m", 290 * 900), ("1h", 290 * 3600)]
    for tf, span in plan:
        if gap > span:
            continue
        try:
            data = fetch_candles(ticker, tf, 300, closed_only=False)
        except MarketDataError:
            continue
        mask = data["ts"] >= since_ms - BAR_SECONDS[tf] * 1000
        if mask.any():
            return {"low": float(data["l"][mask].min()), "high": float(data["h"][mask].max()),
                    "candles": int(mask.sum()), "source": f"OKX {tf} candles"}
    return None


def get_taker_flow(ticker: str) -> dict | None:
    """Taker buy share over the last 1h / 4h / 24h for ALL OKX contracts of the coin (hourly buckets)."""
    ccy = ticker.split("-")[0].upper()
    try:
        rows = _cached(("taker", ccy), lambda: _get("/api/v5/rubik/stat/taker-volume",
                                                    {"ccy": ccy, "instType": "CONTRACTS", "period": "1H"}))
        rows = sorted(((float(r[0]), float(r[1]), float(r[2])) for r in rows), reverse=True)  # [ts, sell, buy]
        if len(rows) < 4:
            return None

        def share(n):
            chunk = rows[:n]
            sell, buy = sum(r[1] for r in chunk), sum(r[2] for r in chunk)
            return buy / (buy + sell) if buy + sell > 0 else None

        return {
            "taker_buy_ratio_1h": share(1),
            "taker_buy_ratio_4h": share(4),
            "taker_buy_ratio_24h": share(24),
        }
    except Exception:
        return None


def get_orderbook_imbalance(ticker: str, depth: int = 50) -> dict | None:
    """Top-of-book depth imbalance & single-level whale wall detection on OKX perpetual swap."""
    try:
        rows = _cached(("books", ticker, depth), lambda: _get(
            "/api/v5/market/books", {"instId": inst_id(ticker, swap=True), "sz": min(depth, 100)}, retries=1, timeout=6.0))
        if not rows:
            return None
        book = rows[0]
        bids = [(float(b[0]), float(b[1])) for b in (book.get("bids") or []) if len(b) >= 2]
        asks = [(float(a[0]), float(a[1])) for a in (book.get("asks") or []) if len(a) >= 2]
        if not bids or not asks:
            return None
        bid_vol = sum(q for _, q in bids)
        ask_vol = sum(q for _, q in asks)
        tot = bid_vol + ask_vol
        if tot <= 0:
            return None
        imbalance = (bid_vol - ask_vol) / tot  # -1.0 to +1.0
        avg_bid = bid_vol / len(bids)
        avg_ask = ask_vol / len(asks)
        max_bid = max(bids, key=lambda x: x[1])
        max_ask = max(asks, key=lambda x: x[1])
        bid_wall = max_bid[1] / avg_bid if avg_bid > 0 else 1.0
        ask_wall = max_ask[1] / avg_ask if avg_ask > 0 else 1.0
        return {
            "orderbook_imbalance": round(imbalance, 4),
            "bid_wall_mult": round(bid_wall, 2),
            "ask_wall_mult": round(ask_wall, 2),
            "bid_wall_price": max_bid[0] if bid_wall >= 4.5 else None,
            "ask_wall_price": max_ask[0] if ask_wall >= 4.5 else None,
        }
    except Exception:
        return None


def get_retail_long_short_ratio(ticker: str) -> float | None:
    """OKX Long/Short Account Ratio (1H).

    High values (>1.8) mean retail accounts are crowded long; low values (<0.75)
    mean retail is crowded short. Institutions frequently fade extreme retail crowds.
    """
    ccy = ticker.split("-")[0].upper()
    try:
        rows = _cached(("ls_ratio", ccy), lambda: _get(
            "/api/v5/rubik/stat/contracts/long-short-account-ratio",
            {"ccy": ccy, "period": "1H"}, retries=1, timeout=6.0))
        if not rows:
            return None
        latest = rows[0]
        val = latest[1] if isinstance(latest, (list, tuple)) else latest.get("ratio")
        return float(val) if val is not None else None
    except Exception:
        return None


def get_coinbase_premium_pct(ticker: str, okx_price: float | None) -> float | None:
    """Coinbase (USD) price vs OKX (USDT) price, in percent. Positive = US spot demand is stronger."""
    if not okx_price:
        return None
    base = ticker.split("-")[0].upper()
    try:
        def produce():
            r = _session.get(f"https://api.exchange.coinbase.com/products/{base}-USD/ticker", timeout=8)
            r.raise_for_status()
            return float(r.json()["price"])
        cb = _cached(("coinbase", base), produce)
        return (cb / okx_price - 1) * 100
    except Exception:
        return None

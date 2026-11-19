"""Fetch public OKX derivatives/order-book context without hiding feed failures."""
from __future__ import annotations

from functools import lru_cache
from typing import Any

import requests


BASE_URL = "https://fapi.binance.com"
BYBIT_BASE_URL = "https://api.bybit.com"
OKX_BASE_URL = "https://www.okx.com"


def _get_json(label: str, path: str, params: dict[str, Any], errors: list[str]) -> Any:
    """Fetch one endpoint independently so one bad feed cannot erase the rest."""
    try:
        response = requests.get(f"{BASE_URL}{path}", params=params, timeout=8)
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict) and payload.get("code") is not None and payload.get("msg"):
            raise ValueError(f"Binance {payload['code']}: {payload['msg']}")
        return payload
    except Exception as exc:
        # Keep diagnostics short enough to safely display in the Discord card.
        detail = " ".join(str(exc).split())[:180]
        errors.append(f"{label}: {detail or type(exc).__name__}")
        return None


def _get_bybit_result(label: str, path: str, params: dict[str, Any], errors: list[str]) -> Any:
    """Fetch one public Bybit V5 endpoint and return its result object."""
    try:
        response = requests.get(f"{BYBIT_BASE_URL}{path}", params=params, timeout=8)
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError as exc:
            body = " ".join((response.text or "").split())[:100]
            raise ValueError(f"non-JSON HTTP {response.status_code} response: {body}") from exc
        if not isinstance(payload, dict):
            raise ValueError("response was not a JSON object")
        if payload.get("retCode") != 0:
            raise ValueError(f"Bybit {payload.get('retCode')}: {payload.get('retMsg', 'request failed')}")
        result = payload.get("result")
        if not isinstance(result, dict):
            raise ValueError("response has no result object")
        return result
    except Exception as exc:
        detail = " ".join(str(exc).split())[:180]
        errors.append(f"Bybit {label}: {detail or type(exc).__name__}")
        return None


def _get_okx_data(label: str, path: str, params: dict[str, Any], errors: list[str]) -> list[dict[str, Any]] | None:
    """Fetch one unsigned OKX v5 public endpoint and return its data rows."""
    try:
        response = requests.get(f"{OKX_BASE_URL}{path}", params=params, timeout=8)
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError as exc:
            body = " ".join((response.text or "").split())[:100]
            raise ValueError(f"non-JSON HTTP {response.status_code} response: {body}") from exc
        if not isinstance(payload, dict):
            raise ValueError("response was not a JSON object")
        if payload.get("code") != "0":
            raise ValueError(f"OKX {payload.get('code')}: {payload.get('msg', 'request failed')}")
        rows = payload.get("data")
        if not isinstance(rows, list):
            raise ValueError("response has no data array")
        return rows
    except Exception as exc:
        detail = " ".join(str(exc).split())[:180]
        errors.append(f"OKX {label}: {detail or type(exc).__name__}")
        return None


def _open_interest_history_point(row: Any) -> tuple[float | None, float | None]:
    """Return (timestamp, open interest) from either OKX history row shape.

    The Rubik history endpoint has returned positional rows such as
    ``[ts, oi, oiUsd]``; other OKX endpoints use objects. Keep parsing
    defensive because this is optional evidence and must not abort a scan.
    """
    if isinstance(row, dict):
        timestamp = row.get("ts")
        value = row.get("oiCcy") or row.get("oi") or row.get("oiUsd")
    elif isinstance(row, (list, tuple)):
        timestamp = row[0] if row else None
        value = row[1] if len(row) > 1 else None
        if value in (None, "") and len(row) > 2:
            value = row[2]
    else:
        return None, None

    try:
        timestamp_number = float(timestamp) if timestamp not in (None, "") else None
    except (TypeError, ValueError):
        timestamp_number = None
    try:
        value_number = float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        value_number = None
    return timestamp_number, value_number


def _fill_okx_fallbacks(data: dict[str, Any], symbol: str, errors: list[str]) -> None:
    """Fill remaining derivatives evidence from unsigned OKX public endpoints.

    OKX symbols use e.g. BTC-USDT-SWAP. A valid public endpoint response is
    independent evidence; an unavailable endpoint is recorded without masking
    data already recovered from Binance or Bybit.
    """
    base = symbol.removesuffix("USDT")
    instrument = f"{base}-USDT-SWAP"
    fallbacks = data["fallbacks"]

    if data.get("funding_rate") is None:
        rows = _get_okx_data("funding rate", "/api/v5/public/funding-rate", {"instId": instrument}, errors)
        if rows:
            try:
                data["funding_rate"] = f"{float(rows[0]['fundingRate']) * 100:.4f}%"
                fallbacks.append("OKX public REST supplied funding rate")
            except (KeyError, TypeError, ValueError):
                errors.append("OKX funding rate: response did not contain a numeric rate")

    if data.get("open_interest") is None:
        rows = _get_okx_data("open interest", "/api/v5/public/open-interest", {"instType": "SWAP", "instId": instrument}, errors)
        if rows:
            try:
                row = next((item for item in rows if item.get("instId") == instrument), rows[0])
                value = row.get("oiCcy") or row.get("oi")
                if value not in (None, ""):
                    data["open_interest"] = f"{float(value):,.2f} {base}"
                    fallbacks.append("OKX public REST supplied current open interest")
            except (TypeError, ValueError):
                errors.append("OKX open interest: response value was not numeric")

    if data.get("open_interest_change_pct") is None:
        rows = _get_okx_data(
            "open-interest history", "/api/v5/rubik/stat/contracts/open-interest-history",
            {"instId": instrument, "period": "1H", "limit": 2}, errors,
        )
        if rows and len(rows) >= 2:
            points = [_open_interest_history_point(row) for row in rows]
            points = [(ts, value) for ts, value in points if value is not None]
            if len(points) >= 2:
                # Use timestamps when present because providers may return
                # either chronological order. If timestamps are absent, keep
                # the endpoint's newest-first convention.
                if all(ts is not None for ts, _ in points):
                    points.sort(key=lambda point: point[0])
                    oldest, newest = points[0][1], points[-1][1]
                else:
                    newest, oldest = points[0][1], points[-1][1]
                if oldest:
                    data["open_interest_change_pct"] = (newest / oldest - 1) * 100
                    fallbacks.append("OKX public REST supplied 1h open-interest change")
            else:
                errors.append("OKX open-interest history: rows had an unsupported shape or missing values")

    if data.get("taker_buy_sell_ratio") is None:
        rows = _get_okx_data("recent trades", "/api/v5/market/trades", {"instId": instrument, "limit": 500}, errors)
        if rows:
            try:
                buys = sum(float(row.get("sz", 0) or 0) for row in rows if row.get("side") == "buy")
                sells = sum(float(row.get("sz", 0) or 0) for row in rows if row.get("side") == "sell")
                if buys + sells:
                    data["taker_buy_sell_ratio"] = buys / sells if sells else 2.0
                    fallbacks.append("OKX public REST supplied taker-flow proxy (latest 500 trades)")
            except (TypeError, ValueError):
                errors.append("OKX recent trades: sizes were not numeric")

    if data.get("liquidation_imbalance") is None:
        # OKX publishes recent filled liquidation orders over public REST.
        # imbalance = (short liquidations - long liquidations) / total, in [-1, 1].
        # Positive: shorts are being force-bought (squeeze, bullish pressure).
        # Negative: longs are being force-sold (cascade, bearish pressure).
        rows = _get_okx_data(
            "liquidation orders", "/api/v5/public/liquidation-orders",
            {"instType": "SWAP", "uly": f"{base}-USDT", "state": "filled", "limit": 100}, errors,
        )
        if rows:
            try:
                import time as _time
                import os as _os
                window_h = float(_os.environ.get('ADMIRAL_LIQ_WINDOW_HOURS', '12') or 12)
                min_fills = int(float(_os.environ.get('ADMIRAL_LIQ_MIN_FILLS', '3') or 3))
                cutoff_ms = (_time.time() - window_h * 3600) * 1000
                long_liq = short_liq = 0.0
                count = 0
                for item in rows:
                    for det in item.get("details", []) or []:
                        try:
                            if float(det.get("ts", 0) or 0) < cutoff_ms:
                                continue
                            notional = abs(float(det.get("sz", 0) or 0) * float(det.get("bkPx", 0) or 0))
                        except (TypeError, ValueError):
                            continue
                        pos_side = str(det.get("posSide", "")).lower()
                        side = str(det.get("side", "")).lower()
                        if pos_side in ("long", "short"):
                            is_long_liq = pos_side == "long"
                        else:
                            is_long_liq = side == "sell"
                        if is_long_liq:
                            long_liq += notional
                        else:
                            short_liq += notional
                        count += 1
                total = long_liq + short_liq
                if count >= min_fills and total > 0:
                    # Few fills = noisy: shrink the imbalance toward 0 until ~10 fills are seen.
                    confidence = min(1.0, count / 10)
                    data["liquidation_imbalance"] = (short_liq - long_liq) / total * confidence
                    fallbacks.append(
                        f"OKX public REST supplied recent liquidation imbalance ({count} fills, last {window_h:g}h, confidence {confidence:.0%})"
                    )
                else:
                    errors.append(f"OKX liquidation orders: only {count} fill(s) in the last {window_h:g}h; too thin to score")
            except (TypeError, ValueError, AttributeError):
                errors.append("OKX liquidation orders: rows had an unsupported shape")

    if data.get("wall_imbalance") is None:
        rows = _get_okx_data("order book", "/api/v5/market/books", {"instId": instrument, "sz": 20}, errors)
        if rows:
            try:
                bids, asks = rows[0].get("bids", []), rows[0].get("asks", [])
                bid_size = sum(float(level[1]) for level in bids[:20])
                ask_size = sum(float(level[1]) for level in asks[:20])
                total = bid_size + ask_size
                if total:
                    data["wall_imbalance"] = (bid_size - ask_size) / total
                    fallbacks.append("OKX public REST supplied top-20 order-book balance proxy")
            except (TypeError, ValueError, IndexError):
                errors.append("OKX order book: levels were malformed")


def _fill_bybit_fallbacks(data: dict[str, Any], symbol: str, errors: list[str]) -> None:
    """Fill available Binance gaps from Bybit public linear-contract data."""
    fallbacks = data["fallbacks"]
    needs_ticker = any(data.get(key) is None for key in (
        "funding_rate", "open_interest", "open_interest_change_pct",
    ))
    if needs_ticker:
        ticker_result = _get_bybit_result(
            "ticker", "/v5/market/tickers",
            {"category": "linear", "symbol": symbol}, errors,
        )
        tickers = ticker_result.get("list", []) if ticker_result else []
        ticker = tickers[0] if tickers else {}
        if ticker:
            if data.get("funding_rate") is None and ticker.get("fundingRate") not in (None, ""):
                try:
                    data["funding_rate"] = f"{float(ticker['fundingRate']) * 100:.4f}%"
                    fallbacks.append("Bybit REST supplied funding rate")
                except (TypeError, ValueError):
                    errors.append("Bybit ticker: funding rate was not numeric")
            if data.get("open_interest") is None and ticker.get("openInterest") not in (None, ""):
                try:
                    data["open_interest"] = f"{float(ticker['openInterest']):,.2f} {symbol[:-4]}"
                    fallbacks.append("Bybit REST supplied current open interest")
                except (TypeError, ValueError):
                    errors.append("Bybit ticker: open interest was not numeric")

    if data.get("open_interest_change_pct") is None:
        oi_result = _get_bybit_result(
            "open-interest history", "/v5/market/open-interest",
            {"category": "linear", "symbol": symbol, "intervalTime": "1h", "limit": 2}, errors,
        )
        points = oi_result.get("list", []) if oi_result else []
        if len(points) >= 2:
            try:
                newest = float(points[0].get("openInterest", 0) or 0)
                oldest = float(points[-1].get("openInterest", 0) or 0)
                if oldest:
                    data["open_interest_change_pct"] = (newest / oldest - 1) * 100
                    fallbacks.append("Bybit REST supplied 1h open-interest change")
            except (TypeError, ValueError):
                errors.append("Bybit open-interest history: values were not numeric")

    if data.get("taker_buy_sell_ratio") is None:
        trades_result = _get_bybit_result(
            "recent trades", "/v5/market/recent-trade",
            {"category": "linear", "symbol": symbol, "limit": 500}, errors,
        )
        trades = trades_result.get("list", []) if trades_result else []
        if trades:
            try:
                buy_size = sum(float(item.get("size", 0) or 0) for item in trades if item.get("side") == "Buy")
                sell_size = sum(float(item.get("size", 0) or 0) for item in trades if item.get("side") == "Sell")
                if buy_size + sell_size:
                    data["taker_buy_sell_ratio"] = buy_size / sell_size if sell_size else 2.0
                    fallbacks.append("Bybit recent trades supplied taker-flow proxy (latest 500 trades)")
            except (TypeError, ValueError):
                errors.append("Bybit recent trades: sizes were not numeric")

    if data.get("wall_imbalance") is None:
        book_result = _get_bybit_result(
            "order book", "/v5/market/orderbook",
            {"category": "linear", "symbol": symbol, "limit": 50}, errors,
        )
        if book_result and "b" in book_result and "a" in book_result:
            try:
                bid_size = sum(float(level[1]) for level in book_result["b"][:20])
                ask_size = sum(float(level[1]) for level in book_result["a"][:20])
                total = bid_size + ask_size
                if total:
                    data["wall_imbalance"] = (bid_size - ask_size) / total
                    fallbacks.append("Bybit REST supplied top-20 order-book balance proxy")
            except (TypeError, ValueError, IndexError):
                errors.append("Bybit order book: levels were malformed")

    if data.get("liquidation_imbalance") is None:
        errors.append("Liquidations remain unavailable: Bybit exposes all-liquidation events on WebSocket, not this REST scan.")


@lru_cache(maxsize=32)
def get_realtime_orderflow(ticker: str) -> dict[str, Any]:
    """Return normalized derivatives evidence and per-feed fetch diagnostics.

    The supplied Actions logs show Binance HTTP 451 and the runner has also
    observed Bybit HTTP 403, so this scan uses only the OKX public REST source.
    OKX does not provide an all-market liquidation heatmap through this REST
    path; swing-pool estimates are supplied separately from OHLCV.
    """
    base_symbol = ticker.split("-")[0].upper().replace("/", "")
    if base_symbol.endswith("USDT"):
        base_symbol = base_symbol[:-4]
    symbol = f"{base_symbol}USDT"
    errors: list[str] = []
    data: dict[str, Any] = {
        "symbol": symbol,
        "funding_rate": None,
        "open_interest": None,
        "taker_buy_sell_ratio": None,
        "top_trader_long_short_ratio": None,
        "wall_imbalance": None,
        "open_interest_change_pct": None,
        "liquidation_imbalance": None,
        "errors": errors,
        "fallbacks": [],
    }

    try:
        _fill_okx_fallbacks(data, symbol, errors)
    except Exception as exc:
        errors.append(f"OKX parser: {type(exc).__name__}: {exc}")

    if data.get("liquidation_imbalance") is None:
        data["fallbacks"].append(
            "Liquidation locations are estimated from repeated 1h/4h/1d swing levels, not live liquidation events"
        )

    data["status"] = "ok" if not errors else f"partial: {len(errors)} feed issue(s)"
    return data
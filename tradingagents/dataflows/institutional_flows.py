"""Daily spot-ETF flow context with keyed API and public no-key fallback.

Reported ETF flows are delayed institutional-demand context, not proof of
live whale-wallet activity or intraday exchange orders. This optional overlay
never blocks a coin scan or changes the trade decision by itself.
"""
from __future__ import annotations

import os
import re
from functools import lru_cache
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Any

import requests


BASE_URL = "https://api.cryptoetf.today/api/v1/flows"
ASSETS = {
    "BTC": "btc", "ETH": "eth", "SOL": "sol", "XRP": "xrp",
    "HYP": "hyp", "DOGE": "doge", "LINK": "link", "AVAX": "avax",
    "HBAR": "hbar", "LTC": "ltc", "BNB": "bnb", "DOT": "dot", "SUI": "sui",
}
PUBLIC_ETF_PAGES = {
    "BTC": "https://farside.co.uk/btc/",
    "ETH": "https://farside.co.uk/eth/",
    "SOL": "https://farside.co.uk/sol/",
    "HYP": "https://farside.co.uk/hyp/",
}


class _TableParser(HTMLParser):
    """Small stdlib parser for the published flow tables; no extra package."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self._table: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag == "table":
            self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None and self._table is not None:
            if self._row:
                self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            self.tables.append(self._table)
            self._table = None


def _flow_number(value: str) -> float | None:
    cleaned = (value or "").strip().replace(",", "").replace("$", "")
    if not cleaned or cleaned in {"-", "—", "–", "N/A", "n/a"}:
        return None
    negative = cleaned.startswith("(") and cleaned.endswith(")")
    if negative:
        cleaned = cleaned[1:-1]
    match = re.search(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)", cleaned)
    if not match:
        return None
    try:
        result = float(match.group(0))
        return -abs(result) if negative else result
    except ValueError:
        return None


def _row_date(value: str) -> date | None:
    value = " ".join((value or "").split())
    for pattern in ("%d %b %Y", "%d %B %Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(value, pattern).date()
        except ValueError:
            pass
    return None


def _farside_flow(base: str) -> dict[str, Any]:
    """Get published daily totals for the supported ETFs without an API key."""
    url = PUBLIC_ETF_PAGES[base]
    response = requests.get(url, headers={"User-Agent": "FutureAdmiralResearch/1.0"}, timeout=8)
    response.raise_for_status()
    parser = _TableParser()
    parser.feed(response.text)
    daily: list[tuple[date, float]] = []
    for table in parser.tables:
        for row in table:
            if len(row) < 3:
                continue
            day = _row_date(row[0])
            total = _flow_number(row[-1])
            if day is None or total is None:
                continue
            # Ignore an unfinished current-day row where every constituent
            # ETF is still a dash but the table displays a temporary 0 total.
            constituents = row[1:-1]
            if constituents and all(item.strip() in {"", "-", "—", "–"} for item in constituents):
                continue
            daily.append((day, total))
    daily.sort(key=lambda item: item[0])
    if not daily:
        raise ValueError("Public ETF-flow page contained no complete dated daily totals.")
    today = datetime.now(timezone.utc).date()
    latest_date, latest_flow = daily[-1]
    recent = [(day, amount) for day, amount in daily if today - timedelta(days=7) <= day <= today]
    if not recent:
        recent = daily[-7:]
    return {
        "latest_date": latest_date.isoformat(),
        "latest_net_flow_usd_m": round(latest_flow, 3),
        "seven_day_net_flow_usd_m": round(sum(amount for _, amount in recent), 3),
        "positive_days": sum(amount > 0 for _, amount in recent),
        "negative_days": sum(amount < 0 for _, amount in recent),
        "source": "Farside Investors public daily ETF-flow table",
        "source_url": url,
    }


def _apply_flow_bias(result: dict[str, Any]) -> dict[str, Any]:
    today = datetime.now(timezone.utc).date()
    try:
        latest = date.fromisoformat(str(result.get("latest_date", ""))[:10])
        age_days = (today - latest).days
    except ValueError:
        age_days = 999
    if age_days < 0:
        result.update(status="STALE", bias="Invalid future-dated flow observation",
                      error="Latest ETF-flow observation is dated in the future; excluded from directional context.")
    elif age_days > 7:
        result.update(status="STALE", bias=f"Stale flow data ({age_days} days old)",
                      error="Latest ETF-flow observation is older than seven days; excluded from directional context.")
    elif float(result.get("seven_day_net_flow_usd_m") or 0) > 0:
        result.update(status="INFLOW", bias="Reported net ETF inflow (bullish context)", error="")
    elif float(result.get("seven_day_net_flow_usd_m") or 0) < 0:
        result.update(status="OUTFLOW", bias="Reported net ETF outflow (bearish context)", error="")
    else:
        result.update(status="FLAT", bias="No net ETF flow over the recent 7-day window", error="")
    return result


@lru_cache(maxsize=32)
def get_institutional_flow(ticker: str, *, api_key: str | None = None) -> dict[str, Any]:
    """Return reported 1d/7d spot-ETF flows or an explicit unavailable state.

    CryptoETF.today remains the primary source when its optional key is set.
    A failed/unconfigured key falls back to the public Farside table for BTC,
    ETH, SOL and HYP. Other tracked assets remain explicitly unavailable if
    the keyed source is not configured; no values are fabricated.
    """
    base = str(ticker or "").strip().upper().split("-")[0].split("/")[0]
    asset = ASSETS.get(base)
    key = (api_key if api_key is not None else os.getenv("CRYPTOETF_API_KEY", "")).strip()
    result: dict[str, Any] = {
        "status": "UNAVAILABLE", "bias": "Unavailable", "asset": base,
        "latest_date": None, "latest_net_flow_usd_m": None,
        "seven_day_net_flow_usd_m": None, "positive_days": 0, "negative_days": 0,
        "source": "CryptoETF.today spot-ETF daily flow series",
        "note": "ETF fund flows are delayed demand context, not real-time whale orders.",
        "error": "",
    }
    if not asset:
        result.update(status="NOT_TRACKED", bias="No ETF flow coverage for this asset",
                      error=f"No supported ETF-flow source lists {base or 'this asset'}.")
        return result

    if key:
        try:
            response = requests.get(
                f"{BASE_URL}/{asset}", headers={"Authorization": f"Bearer {key}"}, timeout=8,
            )
            response.raise_for_status()
            payload = response.json()
            rows = payload.get("days") if isinstance(payload, dict) else None
            if not isinstance(rows, list):
                raise ValueError("ETF-flow response did not contain a daily series.")
            parsed: list[tuple[date, float]] = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                try:
                    parsed.append((date.fromisoformat(str(row.get("date", ""))[:10]), float(row.get("netFlowUsdM"))))
                except (TypeError, ValueError):
                    continue
            parsed.sort()
            if not parsed:
                raise ValueError("ETF-flow response contained no valid daily observations.")
            today = datetime.now(timezone.utc).date()
            latest_date, latest_flow = parsed[-1]
            recent = [(day, amount) for day, amount in parsed if today - timedelta(days=7) <= day <= today]
            result.update({
                "latest_date": latest_date.isoformat(), "latest_net_flow_usd_m": round(latest_flow, 3),
                "seven_day_net_flow_usd_m": round(sum(amount for _, amount in recent), 3),
                "positive_days": sum(amount > 0 for _, amount in recent),
                "negative_days": sum(amount < 0 for _, amount in recent),
                "updated_at": str(payload.get("updatedAt", ""))[:40],
                "source": "CryptoETF.today spot-ETF daily flow series",
            })
            return _apply_flow_bias(result)
        except Exception as exc:
            result["error"] = "CryptoETF.today: " + (" ".join(str(exc).split())[:160] or type(exc).__name__)

    if base in PUBLIC_ETF_PAGES:
        keyed_error = result.get("error", "")
        try:
            public = _farside_flow(base)
            result.update(public)
            result["fallback_from"] = keyed_error
            return _apply_flow_bias(result)
        except Exception as exc:
            detail = " ".join(str(exc).split())[:160] or type(exc).__name__
            result["error"] = (keyed_error + "; " if keyed_error else "") + f"Farside public fallback: {detail}"
            result["source"] = "Farside Investors public daily ETF-flow table (fallback unavailable)"
            return result

    if not key:
        result["error"] = "CRYPTOETF_API_KEY is not configured; no public fallback exists for this asset."
    return result


def format_institutional_flow(flow: dict[str, Any]) -> str:
    """Compact summary for Discord, reports and Sheets."""
    status = flow.get("status", "UNAVAILABLE")
    if status == "NOT_TRACKED":
        return "Not covered by available ETF-flow sources"
    day = flow.get("latest_net_flow_usd_m")
    week = flow.get("seven_day_net_flow_usd_m")
    if day is None or week is None:
        error = str(flow.get("error") or "source/key not configured")
        return f"Unavailable · {error[:150]}"
    freshness = "stale" if status == "STALE" else status.lower()
    return (
        f"{freshness} · latest day ${float(day):+,.1f}M · "
        f"7d ${float(week):+,.1f}M · {flow.get('latest_date') or 'date n/a'} · "
        f"{flow.get('source', 'source n/a')}"
    )

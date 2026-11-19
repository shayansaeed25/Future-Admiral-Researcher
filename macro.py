"""Global Macro, CPI/PPI/Fed & Geopolitical Intelligence Layer (30-Minute Live Radar) - NO API keys needed.

Data sources:
  1. Quantitative Macro (FRED / Yahoo Finance): US 10Y yield, US Dollar Index (DXY),
     WTI Crude Oil (geopolitical & inflation proxy), 10Y Breakeven Inflation (T10YIE),
     VIX Fear Index, Nasdaq Composite + Crypto Fear & Greed Index.
  2. Global News & Geopolitics Radar (refreshed every 30 minutes):
     - US CPI, PPI, PCE, NFP, Federal Reserve rate decisions & liquidity
     - Geopolitics: Iran nuclear/diplomatic talks, US military/conflict news, Middle East
       ceasefire/escalation, Russia/Ukraine, China/US tariffs & sanctions
     - Institutional Crypto: Spot ETF inflows/outflows, whale accumulation, regulatory news

How it works without Analysis Paralysis:
  - Macro & Geopolitical news NEVER freezes the bot all day.
  - Only the immediate FOMC / scheduled CPI release window triggers a brief blackout.
  - Otherwise, Macro + Global News acts as a directional tailwind/headwind modifier:
    bullish macro/de-escalation/cool CPI boosts LONG scores; bearish macro/war escalation/hot CPI
    boosts SHORT scores and trims low-conviction longs.
"""
from __future__ import annotations

import csv
import io
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import requests

# FOMC decision days (statement 14:00 US Eastern) for 2026 - please re-verify on federalreserve.gov.
FOMC_DECISIONS_UTC = [
    "2026-01-28T19:00", "2026-03-18T18:00", "2026-04-29T18:00", "2026-06-17T18:00",
    "2026-07-29T18:00", "2026-09-16T18:00", "2026-10-28T18:00", "2026-12-09T19:00",
]
HEADLINE_FEEDS = {
    "Fed": "https://www.federalreserve.gov/feeds/press_all.xml",
    "Macro_CPI_PPI": "https://news.google.com/rss/search?q=US+CPI+OR+PPI+OR+Federal+Reserve+OR+inflation+OR+interest+rates+OR+NFP&hl=en-US&gl=US&ceid=US:en",
    "Geopolitics_Iran_US": "https://news.google.com/rss/search?q=Iran+talks+OR+US+military+OR+Middle+East+ceasefire+OR+war+OR+tariffs&hl=en-US&gl=US&ceid=US:en",
    "World": "https://feeds.bbci.co.uk/news/world/rss.xml",
    "CNBC_Economy": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=20910258",
    "Crypto": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "Cointelegraph": "https://cointelegraph.com/rss",
}
FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={}"

# Keyword lexicons for deterministic 30-minute Global News & Geopolitical scoring
_GEOPOL_BULLISH = (
    "iran talks progress", "nuclear deal", "diplomatic breakthrough", "ceasefire", "peace deal",
    "truce", "de-escalation", "deescalation", "sanctions lifted", "sanctions relief", "tariff pause",
    "tariff delay", "trade deal", "peace talks", "hostage deal", "diplomacy",
)
_GEOPOL_BEARISH = (
    "airstrike", "missile strike", "missile attack", "military strike", "war escalation",
    "iran attack", "israel strike", "us strike", "strait of hormuz", "oil embargo",
    "nuclear threat", "invasion", "retaliation strike", "emergency sanctions", "trade war",
    "tariff hike", "100% tariff", "martial law",
)
_MACRO_BULLISH = (
    "cpi cools", "cpi lower", "inflation cools", "inflation slows", "disinflation",
    "ppi falls", "ppi lower", "ppi cools", "rate cut", "dovish", "fed pivot",
    "qe", "liquidity injection", "soft landing", "yields fall", "dollar weakens",
)
_MACRO_BEARISH = (
    "hot cpi", "cpi rises", "cpi higher", "inflation surges", "inflation accelerates",
    "sticky inflation", "ppi jumps", "ppi higher", "hot ppi", "rate hike", "hawkish",
    "higher for longer", "stagflation", "recession fear", "yields surge",
)
_CRYPTO_BULLISH = (
    "etf inflow", "record inflow", "blackrock buys", "whale accumulation", "strategic bitcoin reserve",
    "institutional buying", "sec approves", "etf approved", "sovereign wealth", "microstrategy buys",
)
_CRYPTO_BEARISH = (
    "etf outflow", "record outflow", "exchange hack", "bridge hack", "sec lawsuit",
    "mass liquidation", "mt gox", "government sells", "doj sells", "insolvency",
)


def _get(url, timeout=6, tries=1):
    last = None
    for _ in range(tries):
        try:
            r = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0 (future-admiral/3.2)"})
            r.raise_for_status()
            return r.text
        except Exception as exc:
            last = exc
    raise last


def _parse(ts):
    return datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)


def blackout_now(now: datetime | None = None) -> tuple[bool, str]:
    """True during FOMC decision hours (-1h/+1.5h) or +-60 min around ADMIRAL_BLACKOUT_UTC events."""
    now = now or datetime.now(timezone.utc)
    for ts in FOMC_DECISIONS_UTC:
        t = _parse(ts)
        if t - timedelta(hours=2) <= now <= t + timedelta(hours=3):
            return True, f"FOMC decision {t:%Y-%m-%d %H:%M} UTC"
    for ts in [x.strip() for x in os.getenv("ADMIRAL_BLACKOUT_UTC", "").split(",") if x.strip()]:
        try:
            t = _parse(ts)
        except ValueError:
            continue
        if t - timedelta(minutes=90) <= now <= t + timedelta(minutes=90):
            return True, f"scheduled event {t:%Y-%m-%d %H:%M} UTC"
    return False, ""


def parse_fred_csv(text: str) -> list[float]:
    """Numeric values (oldest first) from a FRED fredgraph.csv; '.' / blanks (holidays) are skipped."""
    rows = list(csv.reader(io.StringIO(text)))
    out = []
    for row in rows[1:]:
        if len(row) >= 2:
            try:
                out.append(float(row[1]))
            except ValueError:
                continue
    return out


YAHOO = {"DGS10": "^TNX", "DTWEXBGS": "DX-Y.NYB", "DCOILWTICO": "CL=F", "VIXCLS": "^VIX", "NASDAQCOM": "^IXIC"}
BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
FETCH_BUDGET_SECONDS = 45


def parse_yahoo_chart(payload: dict) -> list[float]:
    res = payload["chart"]["result"][0]
    closes = res["indicators"]["quote"][0]["close"]
    return [float(c) for c in closes if c is not None]


def parse_fred_api(payload: dict) -> list[float]:
    vals = []
    for obs in payload.get("observations", []):
        try:
            vals.append(float(obs["value"]))
        except (KeyError, TypeError, ValueError):
            continue
    return vals[::-1]  # API returned newest first


def _try(label: str, fn, errors: list[str], deadline: float) -> list[float]:
    if time.time() > deadline:
        errors.append(f"{label}: skipped (time budget used)")
        return []
    try:
        vals = fn()
        if vals:
            return vals
        errors.append(f"{label}: empty")
    except Exception as exc:
        errors.append(f"{label}: {type(exc).__name__} {str(exc)[:70]}")
    return []


def fetch_series(series_id: str, errors: list[str], deadline: float | None = None) -> list[float]:
    """Try sources in order of reliability from GitHub runners; one short attempt each."""
    deadline = deadline or time.time() + FETCH_BUDGET_SECONDS
    key = os.getenv("FRED_API_KEY", "").strip()
    if key:
        url = (f"https://api.stlouisfed.org/fred/series/observations?series_id={series_id}&api_key={key}"
               "&file_type=json&sort_order=desc&limit=70")
        vals = _try(f"FREDapi {series_id}", lambda: parse_fred_api(requests.get(url, timeout=8).json()), errors, deadline)
        if vals:
            return vals
    sym = YAHOO.get(series_id)
    if sym:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(sym)}?range=3mo&interval=1d"
        vals = _try(f"Yahoo {sym}", lambda: parse_yahoo_chart(requests.get(url, timeout=8, headers={"User-Agent": BROWSER_UA}).json()), errors, deadline)
        if vals:
            if series_id == "DGS10" and vals[-1] > 15:  # some feeds quote the 10Y yield x10
                vals = [v / 10 for v in vals]
            return vals[-60:]
    return _try(f"FREDcsv {series_id}", lambda: parse_fred_csv(_get(FRED_CSV.format(series_id), timeout=8, tries=1))[-60:], errors, deadline)


def _vote(value: float, risk_off_at: float, risk_on_at: float, higher_is_risk_off: bool = True) -> int:
    if higher_is_risk_off:
        return -1 if value >= risk_off_at else 1 if value <= risk_on_at else 0
    return -1 if value <= risk_off_at else 1 if value >= risk_on_at else 0


def evaluate_factors(series: dict[str, list[float]]) -> tuple[int, list[str], list[str]]:
    """(total vote, short notes, story lines) from the raw series; pure function, easy to test."""
    notes, story, total = [], [], 0
    v = series.get("DGS10")
    if v and len(v) >= 6:
        chg = v[-1] - v[-6]
        vote = _vote(chg, 0.15, -0.15)
        total += vote
        notes.append(f"US10Y {v[-1]:.2f}% ({chg:+.2f}pp 5d)")
        if vote:
            story.append("yields " + ("rising -> tighter money" if vote < 0 else "falling -> easier money"))
    v = series.get("DTWEXBGS")
    if v and len(v) >= 6:
        chg = (v[-1] / v[-6] - 1) * 100
        vote = _vote(chg, 0.7, -0.7)
        total += vote
        notes.append(f"Dollar {chg:+.1f}% 5d")
        if vote:
            story.append("dollar " + ("strengthening" if vote < 0 else "weakening"))
    v = series.get("DCOILWTICO")
    oil_vote = 0
    if v and len(v) >= 21:
        chg = (v[-1] / v[-21] - 1) * 100
        oil_vote = _vote(chg, 10.0, -10.0)
        total += oil_vote
        notes.append(f"Oil ${v[-1]:.0f} ({chg:+.0f}% 20d)")
    v = series.get("T10YIE")
    be_vote = 0
    if v and len(v) >= 21:
        chg = v[-1] - v[-21]
        be_vote = _vote(chg, 0.10, -0.10)
        total += be_vote
        notes.append(f"Inflation expectations {v[-1]:.2f}% ({chg:+.2f}pp 20d)")
    if oil_vote < 0 and be_vote < 0:
        story.append("oil up + inflation expectations up -> Fed cuts less likely")
    elif oil_vote > 0 and be_vote > 0:
        story.append("oil down + inflation expectations down -> room for Fed easing")
    v = series.get("VIXCLS")
    if v:
        vote = _vote(v[-1], 25.0, 16.0)
        total += vote
        notes.append(f"VIX {v[-1]:.1f}")
        if vote < 0:
            story.append("fear gauge (VIX) elevated")
    v = series.get("NASDAQCOM")
    if v and len(v) >= 6:
        chg = (v[-1] / v[-6] - 1) * 100
        vote = _vote(chg, -3.0, 3.0, higher_is_risk_off=False)
        total += vote
        notes.append(f"Nasdaq {chg:+.1f}% 5d")
        if vote:
            story.append("US tech stocks " + ("falling" if vote < 0 else "rising"))
    return total, notes, story


def score_global_headlines(headlines: list[str]) -> dict:
    """Analyze global RSS headlines for CPI/PPI/Fed, US/Iran/Middle East geopolitics, and institutional catalysts."""
    geo_bull, geo_bear = 0, 0
    mac_bull, mac_bear = 0, 0
    cry_bull, cry_bear = 0, 0
    matched_geo: list[str] = []
    matched_macro: list[str] = []
    matched_crypto: list[str] = []

    for raw in headlines:
        lower = raw.lower()
        for kw in _GEOPOL_BULLISH:
            if kw in lower:
                geo_bull += 1
                if len(matched_geo) < 2:
                    matched_geo.append(f"Geopolitics+: {kw}")
                break
        for kw in _GEOPOL_BEARISH:
            if kw in lower:
                geo_bear += 1
                if len(matched_geo) < 2:
                    matched_geo.append(f"Geopolitics-: {kw}")
                break
        for kw in _MACRO_BULLISH:
            if kw in lower:
                mac_bull += 1
                if len(matched_macro) < 2:
                    matched_macro.append(f"Macro+: {kw}")
                break
        for kw in _MACRO_BEARISH:
            if kw in lower:
                mac_bear += 1
                if len(matched_macro) < 2:
                    matched_macro.append(f"Macro-: {kw}")
                break
        for kw in _CRYPTO_BULLISH:
            if kw in lower:
                cry_bull += 1
                if len(matched_crypto) < 2:
                    matched_crypto.append(f"Inst+: {kw}")
                break
        for kw in _CRYPTO_BEARISH:
            if kw in lower:
                cry_bear += 1
                if len(matched_crypto) < 2:
                    matched_crypto.append(f"Inst-: {kw}")
                break

    bull_pts = geo_bull * 1.2 + mac_bull * 1.2 + cry_bull * 1.0
    bear_pts = geo_bear * 1.3 + mac_bear * 1.2 + cry_bear * 1.0
    net = bull_pts - bear_pts
    denom = max(2.5, bull_pts + bear_pts)
    sentiment = max(-1.0, min(1.0, net / denom))
    vote = 1 if sentiment >= 0.25 else -1 if sentiment <= -0.25 else 0
    tags = matched_geo + matched_macro + matched_crypto
    return {
        "news_sentiment": round(sentiment, 3),
        "news_vote": vote,
        "geopolitical_bias": 1 if geo_bull > geo_bear else -1 if geo_bear > geo_bull else 0,
        "cpi_ppi_bias": 1 if mac_bull > mac_bear else -1 if mac_bear > mac_bull else 0,
        "catalysts": tags[:4],
    }


def get_headlines(per_feed: int = 4, budget_seconds: float = 15.0) -> list[str]:
    titles = []
    deadline = time.time() + budget_seconds
    for name, url in HEADLINE_FEEDS.items():
        if time.time() > deadline:
            break
        try:
            root = ET.fromstring(_get(url, timeout=4, tries=1))
            items = root.findall(".//item")[:per_feed]
            for i in items:
                title = re.sub(r"\s+", " ", (i.findtext("title") or "")).strip()
                if len(title) > 8:
                    titles.append(f"[{name}] {title}")
        except Exception:
            continue
    return titles


def get_macro(now: datetime | None = None) -> dict:
    """{'risk': -1|0|1, 'notes': [...], 'story': [...], 'blackout': bool, 'blackout_reason': str, 'fear_greed': int|None}"""
    now = now or datetime.now(timezone.utc)
    out = {"risk": 0, "notes": [], "story": [], "fear_greed": None, "total": 0, "errors": [],
           "news_sentiment": 0.0, "catalysts": [], "headlines": []}
    out["blackout"], out["blackout_reason"] = blackout_now(now)
    series = {}
    deadline = time.time() + FETCH_BUDGET_SECONDS
    for sid in ("DGS10", "DTWEXBGS", "DCOILWTICO", "T10YIE", "VIXCLS", "NASDAQCOM"):
        vals = fetch_series(sid, out["errors"], deadline)
        if vals:
            series[sid] = vals
    total, notes, story = evaluate_factors(series)
    try:
        fg = int(requests.get("https://api.alternative.me/fng/?limit=1", timeout=5).json()["data"][0]["value"])
        out["fear_greed"] = fg
        notes.append(f"Fear&Greed {fg}")
        if fg <= 22:
            story.append("extreme retail fear (contrarian whale accumulation zone)")
        elif fg >= 82:
            story.append("extreme retail greed (contrarian distribution risk)")
    except Exception:
        pass

    # Fetch 30-minute global macro, CPI/PPI, & geopolitical (US/Iran/conflict) headlines
    try:
        headlines = get_headlines(per_feed=4, budget_seconds=12.0)
        out["headlines"] = headlines[:16]
        if headlines:
            news_res = score_global_headlines(headlines)
            out["news_sentiment"] = news_res["news_sentiment"]
            out["catalysts"] = news_res["catalysts"]
            total += news_res["news_vote"]
            if news_res["catalysts"]:
                notes.append("News: " + ", ".join(news_res["catalysts"][:2]))
            if news_res["geopolitical_bias"] < 0:
                story.append("geopolitical tension elevated (US/Iran/conflict headlines)")
            elif news_res["geopolitical_bias"] > 0:
                story.append("geopolitical de-escalation / talks progress")
            if news_res["cpi_ppi_bias"] < 0:
                story.append("inflation/Fed headlines hawkish (CPI/PPI/rates pressure)")
            elif news_res["cpi_ppi_bias"] > 0:
                story.append("inflation/Fed headlines supportive (cooling CPI/PPI or easing)")
    except Exception:
        pass

    out["notes"], out["story"], out["total"] = notes, story, total
    out["risk"] = -1 if total <= -2 else 1 if total >= 2 else 0
    return out


CACHE_OK_HOURS, CACHE_FAIL_HOURS = 20, 3
NEWS_REFRESH_MINUTES = 25


def get_macro_cached(state: dict | None = None, now: datetime | None = None) -> dict:
    """Fetch macro data & global 30-minute news radar.

    When ADMIRAL_MACRO_CACHE_HOURS is set (e.g. '0.42' = 25 min in production cron),
    the full macro + global news scan refreshes every 30-minute run.
    """
    now = now or datetime.now(timezone.utc)
    cache = (state or {}).get("macro_cache") or {}
    env_ok_hours = os.getenv("ADMIRAL_MACRO_CACHE_HOURS", "").strip()
    ok_hours = float(env_ok_hours) if env_ok_hours else CACHE_OK_HOURS
    try:
        age_h = (now - datetime.fromisoformat(cache["fetched_utc"])).total_seconds() / 3600
        limit = ok_hours if cache.get("ok") else CACHE_FAIL_HOURS
        if age_h < limit and cache.get("data"):
            data = dict(cache["data"])
            data["blackout"], data["blackout_reason"] = blackout_now(now)  # always live
            return data
    except Exception:
        pass
    data = get_macro(now)
    ok = bool(data.get("notes")) and any(("US10Y" in n or "Dollar" in n or "Oil" in n) for n in data["notes"])
    if state is not None:
        state["macro_cache"] = {"fetched_utc": now.isoformat(), "ok": ok, "data": data}
    return data

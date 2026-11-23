"""
admiral_core.py  (v4.0 - Institutional Terminal + Live Rolling Backtest Display)
--------------------------------------------------------------------------------
Shared by the live scanner (cloud_run.py) and the on-demand tool (analyze_now.py).
"""
from __future__ import annotations

import os
import re
import textwrap
import time
from datetime import datetime, timezone

import requests

import etf_flow
import llm_verdict
import macro as macro_mod
import market_data as md
import audit_journal
import signal_engine as se
from admiral_utils import format_pkt, now_pkt
from paper_trading import configured_notional

DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK")


# --- position sizing ---------------------------------------------------------

def _env_float(name, default):
    raw = os.getenv(name, "").strip()
    try:
        return float(raw) if raw else default
    except ValueError:
        return default


ACCOUNT_CAPITAL_USDT = _env_float("ACCOUNT_CAPITAL_USDT", _env_float("ACCOUNT_CAPITAL_USD", 1000.0))
RISK_PERCENT_PER_TRADE = _env_float("RISK_PERCENT_PER_TRADE", 1.0)
PAPER_TRADE_NOTIONAL_USDT = configured_notional()
MAX_HOLD_HOURS = _env_float("ADMIRAL_MAX_HOLD_HOURS", 72)


def position_size(entry, sl):
    risk_amount = ACCOUNT_CAPITAL_USDT * (RISK_PERCENT_PER_TRADE / 100)
    risk_per_unit = abs(entry - sl)
    if risk_per_unit <= 0:
        return None
    units = risk_amount / risk_per_unit
    max_notional = ACCOUNT_CAPITAL_USDT * _env_float("ADMIRAL_MAX_LEVERAGE", 3.0)
    capped = units * entry > max_notional
    if capped:
        units = max_notional / entry
        risk_amount = units * risk_per_unit
    return {"units": units, "usd_value": units * entry, "risk_amount": risk_amount, "capped": capped}


# --- small formatting helpers -----------------------------------------------

def _price(value):
    if value is None:
        return "N/A"
    return f"{float(value):,.8f}".rstrip("0").rstrip(".")


def _short_text(text, limit=850):
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    if not cleaned:
        return "-"
    return textwrap.shorten(cleaned, width=limit, placeholder="…")


def _pct(target, entry):
    return (target - entry) / entry * 100 if entry else 0.0


# --- prices / candles --------------------------------------------------------

def get_current_price(ticker, health_events=None):
    """Returns (price, reason). price is None on total failure."""
    price = md.get_price(ticker)
    if price:
        return price, None
    message = f"OKX returned no price for {ticker}"
    if health_events is not None:
        health_events.append(f"CRITICAL: no price source returned data for {ticker}: {message}")
    return None, message


def get_price_range_since(ticker, since_utc=None):
    """High/low traded since the previous position check (None if the feed fails)."""
    if since_utc:
        since = datetime.fromisoformat(str(since_utc).replace("Z", "+00:00"))
        start_ms = int(since.timestamp() * 1000)
    else:
        start_ms = int((time.time() - 7200) * 1000)
    return md.get_range_since(ticker, start_ms)


_ETF_SCALE = {"BTC": 500.0, "ETH": 200.0}


def etf_signal(ticker):
    """(-1..+1 score, raw dict) from spot-ETF flows: the coin's own ETF if tracked, else BTC's as the market-wide tide."""
    base = ticker.split("-")[0]
    for t in ([ticker] if base != "BTC" else []) + ["BTC-USDT"]:
        try:
            flow = etf_flow.get_institutional_flow(t)
        except Exception:
            continue
        week = flow.get("seven_day_net_flow_usd_m")
        if week is not None and flow.get("status") != "NOT_TRACKED":
            scale = _ETF_SCALE.get(t.split("-")[0], 25.0)
            return max(-1.0, min(1.0, float(week) / scale)), {**flow, "used_ticker": t}
    return None, {}


def load_context(tickers=None, state=None):
    """Market-wide context shared by every coin in one run: BTC 4H trend + 30-min macro/geopolitical view."""
    ctx = {"btc_4h": None, "macro": {}}
    try:
        try:
            ctx["macro"] = macro_mod.get_macro_cached(state)
        except TypeError:
            ctx["macro"] = macro_mod.get_macro()
    except Exception as exc:
        print(f"[!] Macro layer unavailable ({str(exc)[:100]}); treated as neutral.")
    try:
        btc_c4 = md.fetch_candles("BTC-USDT", "4h", 300)
        ctx["btc_4h"] = se.btc_trend_from_candles(btc_c4)
        btc_c1 = None
        try:
            btc_c1 = md.fetch_candles("BTC-USDT", "1h", 300)
            ctx["btc_1h"] = se.btc_trend_from_candles(btc_c1)
        except Exception:
            ctx["btc_1h"] = None
        ctx["btc_snapshot"] = se.btc_snapshot_from_candles(btc_c1, btc_c4)
    except Exception as exc:
        print(f"[!] BTC context unavailable ({str(exc)[:100]}); BTC filter skipped this run.")
    return ctx


# --- Discord -------------------------------------------------------------------

def send_discord(embed):
    if not DISCORD_WEBHOOK:
        print("::error title=Discord notification unavailable::DISCORD_WEBHOOK secret is not configured.")
        return False
    for attempt in range(2):
        try:
            response = requests.post(DISCORD_WEBHOOK, json={"embeds": [embed]}, timeout=10)
            if response.status_code == 429 and attempt == 0:
                time.sleep(min(5.0, float(response.headers.get("Retry-After", "2") or 2)))
                continue
            response.raise_for_status()
            return True
        except Exception as exc:
            print(f"::error title=Discord notification delivery failed::{str(exc)[:180]}")
            return False
    return False


def discord_failure_summary(error):
    return f"Analysis mukammal nahi hui: {str(error)[:200]}. Koi signal issue nahi hua."


_TF_BADGE = {"BULLISH": "🟢 BULL", "BEARISH": "🔴 BEAR", "MIXED": "⚪ MIXED"}


def _regime_text(regime):
    tf = regime.get("timeframe_trends", {})
    b1 = _TF_BADGE.get(tf.get("1h"), tf.get("1h", "n/a"))
    b4 = _TF_BADGE.get(tf.get("4h"), tf.get("4h", "n/a"))
    bd = _TF_BADGE.get(tf.get("1d"), tf.get("1d", "n/a"))
    return (f"**Trends:** `1H: {b1}` · `4H: {b4}` · `1D: {bd}`\n"
            f"**Structure:** `{regime.get('trend', 'n/a')}` · **Momentum:** `{regime.get('momentum', 'n/a')}` · "
            f"**Volume Flow:** `{regime.get('accumulation_distribution', 'n/a')}`\n"
            f"**Metrics:** `1H RSI: {regime.get('rsi_1h', 'n/a')}` · `4H ADX: {regime.get('adx_4h', 'n/a')}` · `4H CMF: {regime.get('cmf_4h', 'n/a')}`")


def _macro_text(result):
    m = result.get("macro") or {}
    tone = {1: "🟢 RISK-ON", -1: "🔴 RISK-OFF", 0: "⚪ NEUTRAL"}.get(m.get("risk", 0), "⚪ NEUTRAL")
    extra = ("\n**News Catalysts:** `" + ", ".join(m.get("catalysts", [])[:3]) + "`") if m.get("catalysts") else ""
    return f"**Regime:** {tone} · " + (" · ".join(m.get("notes", [])) or "macro data unavailable") + extra


_SETUP_NAMES = {
    "pullback": "Trend Pullback Reclaim (EMA20/VWAP)",
    "breakout": "Institutional Volume Breakout",
    "momentum": "1H Institutional Impulse (VWAP+MACD)",
    "whale_reversal": "Whale Absorption & RSI Divergence",
    "smc_sweep": "Smart Money Liquidity Sweep (SMC)",
}


def build_embed(result, source_label, verdict=None):
    """Discord card for a decision (signal or no-signal)."""
    ticker, action, cmp = result["ticker"], result["action"], result["cmp"]
    regime = result["market_regime"]
    footer = {"text": f"{_short_text(source_label, 50)} · {format_pkt(fmt='%Y-%m-%d %H:%M PKT')} · Institutional Research Desk"}
    if action in ("BUY", "SELL"):
        lv = result["levels"]
        icon = "🟢" if action == "BUY" else "🔴"
        side_label = "LONG / BUY" if action == "BUY" else "SHORT / SELL"
        sizing = position_size(cmp, lv["sl"])
        coin = ticker.split("-")[0]
        setup_pretty = _SETUP_NAMES.get(result["setup"], result["setup"].replace("_", " ").title())
        bt = result.get("live_backtest") or {}
        bt_line = (
            f"✅ **PASS** — `{bt['win_rate']:.0f}% Win Rate` (`{bt['wins']}/{bt['trades']}` wins) · "
            f"`{bt['sl_rate']:.0f}% SL Rate` · `{bt['avg_r']:+.2f}R Avg`"
            if bt.get("trades") else "✅ **PASS** — Clean regime (0 recent SLs on this setup)"
        )
        fields = [
            {"name": "Direction", "value": f"**{icon} {side_label}**", "inline": True},
            {"name": "Conviction Score", "value": f"**Grade {result['grade']} ({result['quality']:.0f}/100)**", "inline": True},
            {"name": "Setup Type", "value": f"**{setup_pretty}**", "inline": True},
            {"name": "Entry Price (Market)", "value": f"`{_price(cmp)} USDT`", "inline": True},
            {"name": "Stop Loss (Capped <=3.25%)", "value": f"`{_price(lv['sl'])} USDT` (`{_pct(lv['sl'], cmp):+.2f}%`)", "inline": True},
            {"name": "Risk : Reward", "value": f"**1 : {lv['rr']:.1f}** _(Avg TP1–TP3)_", "inline": True},
            {"name": "🎯 Take-Profit Targets", "value": "\n".join(
                f"• **TP{n} ({abs(lv[f'tp{n}'] - cmp) / lv['risk']:.1f}R):** `{_price(lv[f'tp{n}'])} USDT` (`{_pct(lv[f'tp{n}'], cmp):+.2f}%`)"
                for n in (1, 2, 3)), "inline": False},
            {"name": "🧪 Live Rolling Backtest (Recent Candles)", "value": bt_line, "inline": False},
            {"name": "🧠 Institutional & Technical Confluence", "value": _short_text("\n".join(f"• {r}" for r in result["reasons"]), 600), "inline": False},
            {"name": "📊 Multi-Timeframe Regime", "value": _short_text(_regime_text(regime), 500), "inline": False},
            {"name": "🌍 Global Macro & Geopolitics (30m Radar)", "value": _short_text(_macro_text(result), 350), "inline": False},
            {"name": "🛡️ Execution & Risk Protocol", "value": (
                f"Scale out **1/3** at each target (TP1, TP2, TP3). Once **TP1 (1R)** hits, move Stop-Loss to **Entry (Breakeven)**. "
                f"Time stop: **{MAX_HOLD_HOURS:g}h**."), "inline": False},
        ]
        if sizing:
            fields.append({"name": "💼 Position Sizing Reference", "value": (
                f"`{sizing['units']:.6g} {coin}` (`≈ ${sizing['usd_value']:.2f} USDT`) · Risking `${sizing['risk_amount']:.2f}` "
                f"(`{RISK_PERCENT_PER_TRADE:g}%` of `${ACCOUNT_CAPITAL_USDT:,.0f}` capital)"
                + (" _(capped by max leverage)_" if sizing.get("capped") else "")), "inline": False})
        fields.append({"name": "📒 Paper Ledger Allocation", "value": (
            f"`${PAPER_TRADE_NOTIONAL_USDT:.2f} USDT` notional · Equal 1/3 exits at TP1/TP2/TP3."), "inline": False})
        if verdict:
            fields.append({"name": "📝 Research Desk Verdict (Roman Urdu)", "value": _short_text(verdict, 700), "inline": False})
        return {"author": {"name": "Future Admiral Research Desk — Trade Signal"}, "title": f"{icon} {ticker} — {action} (Grade {result['grade']} · {result['quality']:.0f}/100)",
                "description": f"**{setup_pretty}** confirmed on closed 1H candle & validated on recent market candles.",
                "color": 0x2ECC71 if action == "BUY" else 0xE74C3C, "fields": fields, "footer": footer}
    fields = [
        {"name": "Decision", "value": "**⏳ NO SIGNAL (WAIT)**", "inline": True},
        {"name": "Current Price", "value": f"`{_price(cmp)} USDT`", "inline": True},
        {"name": "Desk Diagnostic (Why Waiting)", "value": _short_text("\n".join(f"• {b}" for b in result.get("blockers", [])[:4]) or "No setup", 600), "inline": False},
        {"name": "📊 Multi-Timeframe Regime", "value": _short_text(_regime_text(regime), 500), "inline": False},
        {"name": "🌍 Global Macro & Geopolitics (30m Radar)", "value": _short_text(_macro_text(result), 350), "inline": False},
    ]
    return {"author": {"name": _short_text(source_label, 80)}, "title": f"🔍 {ticker} — On-Demand Desk Check",
            "description": "No high-conviction 1H institutional or backtest-validated setup on the current bar.",
            "color": 0x99A3AF, "fields": fields, "footer": footer}


def add_verdict(result, source_label="Future Admiral Research Desk"):
    """Attach the optional one-call LLM note to an already-approved signal."""
    if result.get("action") not in ("BUY", "SELL") or result.get("verdict"):
        return result
    verdict = llm_verdict.get_verdict(result["ticker"], result["action"], result["quality"], result["setup"],
                                      result["reasons"], {**result["levels"], "entry": result["cmp"]})
    if verdict:
        result["verdict"] = verdict
        result["embed"] = build_embed(result, source_label, verdict)
    return result


# --- the one shared "analyze this coin right now" routine -----------------------

def analyze_ticker(ticker, key_offset=0, source_label="Future Admiral Research Desk", report_id=None,
                   current_price=None, ctx=None):
    """Evaluate one coin on its last CLOSED 1H candle. ~6 public API calls, 0 LLM calls."""
    from admiral_utils import normalize_ticker
    ticker = normalize_ticker(ticker)
    health = []
    cmp = current_price
    if not cmp:
        cmp, err = get_current_price(ticker, health_events=health)
        if not cmp:
            return {"ok": False, "error": f"Price feed: {err}", "action": None, "cmp": None, "sl": None, "tp": None,
                    "embed": None, "data_health": health}
    try:
        c1 = md.fetch_candles(ticker, "1h", 300)
        c4 = md.fetch_candles(ticker, "4h", 300)
        cd = md.fetch_candles(ticker, "1d", 300)
    except md.MarketDataError as exc:
        return {"ok": False, "error": str(exc)[:250], "action": None, "cmp": cmp, "sl": None, "tp": None,
                "embed": None, "data_health": health}
    age_h = (time.time() * 1000 - (c1["ts"][-1] + 3_600_000)) / 3_600_000
    if age_h > 3:
        return {"ok": False, "error": f"{ticker}: latest closed 1H candle is {age_h:.1f}h old (stale feed)", "action": None,
                "cmp": cmp, "sl": None, "tp": None, "embed": None, "data_health": health}

    funding = md.get_funding_pct(ticker)
    oi = md.get_oi_change_pct(ticker)
    if funding is None:
        health.append(f"{ticker}: funding rate unavailable (neutral score used)")
    if oi is None:
        health.append(f"{ticker}: open-interest change unavailable (neutral score used)")
    btc_trend = (ctx or {}).get("btc_4h")
    btc_trend_1h = (ctx or {}).get("btc_1h")
    macro = (ctx or {}).get("macro") or {}
    taker = md.get_taker_flow(ticker)
    flow = dict(taker) if taker else {}
    if taker is not None:
        ob = md.get_orderbook_imbalance(ticker)
        if ob:
            flow.update(ob)
        ls = md.get_retail_long_short_ratio(ticker)
        if ls is not None:
            flow["retail_ls_ratio"] = ls
    premium = md.get_coinbase_premium_pct(ticker, cmp)
    if premium is not None:
        flow["coinbase_premium_pct"] = premium
    etf_score, etf_raw = etf_signal(ticker)
    if etf_score is not None:
        flow["etf_flow_score"] = etf_score
    if not flow:
        health.append(f"{ticker}: institutional-flow proxies unavailable (neutral score used)")

    res = se.evaluate_live(c1, c4, cd, price=cmp, ticker=ticker, btc_trend_4h=btc_trend,
                           btc_trend_1h=btc_trend_1h, funding_pct=funding, oi_change_pct=oi,
                           flow=flow or None, macro=macro)
    action = res["action"]
    sign = 1 if action == "BUY" else -1 if action == "SELL" else 0
    lv = res["levels"] if action in ("BUY", "SELL") else None
    result = {
        "ok": True, "error": None, "ticker": ticker, "action": action, "cmp": cmp,
        "bar_ts": int(c1["ts"][-1]),
        "sl": lv["sl"] if lv else None, "tp": lv["tp1"] if lv else None,
        "tp1": lv["tp1"] if lv else None, "tp2": lv["tp2"] if lv else None, "tp3": lv["tp3"] if lv else None,
        "levels": lv, "grade": res["grade"], "setup": res["setup"], "quality": res["score"],
        "live_backtest": res.get("live_backtest"),
        "reasons": res["reasons"], "blockers": res["blockers"], "components": res["components"],
        "market_regime": res["regime"], "risk_basis": lv["basis"] if lv else "",
        "no_trade_reason": "; ".join(res["blockers"][:3]),
        "matrix": {"score": round(sign * res["score"] / 100, 4), "coverage": res["coverage"],
                   "institutional_flow": {}, "market_regime": res["regime"],
                   "orderflow_proxies": {
                       "taker_buy_sell_ratio": (round(flow["taker_buy_ratio_4h"] / (1 - flow["taker_buy_ratio_4h"]), 3)
                                                if flow.get("taker_buy_ratio_4h") not in (None, 1) else ""),
                       "orderbook_imbalance": flow.get("orderbook_imbalance", ""),
                   }},
        "flow": flow, "macro": macro, "etf": etf_raw,
        "candles_1h_tail": audit_journal.candle_tail(c1, 5),
        "data_health": health, "report_path": None, "institutional_flow": etf_raw, "orderflow_proxies": {},
    }
    result["embed"] = build_embed(result, source_label)
    return result

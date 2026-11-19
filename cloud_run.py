"""Hourly / 30-Minute Live Scanner (v4.0 — Live Rolling Backtest + Anti-Doubling + Rich TP/SL Alerts)."""
import os
import time
import uuid
from datetime import datetime, timezone

import engine_state
import market_data as md
from admiral_core import (MAX_HOLD_HOURS, add_verdict, analyze_ticker, etf_signal, get_current_price,
                          get_price_range_since, load_context, send_discord)
from admiral_utils import format_pkt, now_pkt
from google_sheets_sync import (load_cloud_state, prepare as prepare_sheet, record_health_event,
                                record_scan_event, save_cloud_state, sync_open_positions, update_position, write_board)
import etf_flow
from health_log import record_health
from paper_trading import apply_paper_outcome, mark_to_market, paper_signal_fields
from position_state import evaluate_position_range, load_positions, save_positions
from watchlist_manager import load_watchlist

LOG_NO_SIGNAL = os.getenv("ADMIRAL_LOG_NO_SIGNAL", "0") == "1"
SHEET_EVERY_RUN = os.getenv("ADMIRAL_SHEET_EVERY_RUN", "1") == "1"
HEARTBEAT_HOUR_PKT = int(float(os.getenv("ADMIRAL_HEARTBEAT_HOUR_PKT", "9") or 9))


def scan_window_open(now=None):
    if os.getenv("ADMIRAL_FORCE_SCAN", "0") == "1":
        return True
    try:
        from signal_engine import default_config
        if int(default_config()["setup_tf"]) != 4:
            return True
    except Exception:
        return True
    now = now or datetime.now(timezone.utc)
    return now.hour % 4 in (0, 1)


def _fmt_price(value):
    value = float(value)
    digits = 2 if abs(value) >= 1000 else 4 if abs(value) >= 1 else 6 if abs(value) >= 0.01 else 8
    return f"{value:,.{digits}f}"


def _notify_stage(ticker, position, event, price, low=None, high=None):
    labels = {
        "SL_HIT": "🛑 Stop Loss Hit (Trade Closed)",
        "BE_EXIT": "🛡️ Closed at Breakeven (TP1 Profit Banked)",
        "TP1_HIT": "🎯 TP1 Hit (+1R Banked · SL Moved to Breakeven!)",
        "TP2_HIT": "🎯 TP2 Hit (+2R Banked!)",
        "TP3_HIT": "🏆 TP3 Final Target Hit (+3R Full Win!)",
        "TIME_EXIT": f"⏱️ Time exit ({MAX_HOLD_HOURS:g}h limit)",
    }
    entry = float(position.get("entry") or price)
    realized = float(position.get("paper_realized_pnl_usdt", 0.0) or 0.0)
    total_pnl = float(position.get("paper_total_pnl_usdt", realized) or 0.0)
    ret_pct = float(position.get("paper_return_pct", 0.0) or 0.0) * 100.0
    trigger_px = (
        position.get("tp1") if event == "TP1_HIT" else
        position.get("tp2") if event == "TP2_HIT" else
        position.get("tp3") if event == "TP3_HIT" else
        position.get("sl") if event in ("SL_HIT", "BE_EXIT") else price
    )
    move_pct = ((float(trigger_px) - entry) / entry * 100.0 * (1 if position.get("action") == "BUY" else -1)) if entry else 0.0
    rng_str = f" · **Range Checked:** `{_fmt_price(low)} – {_fmt_price(high)}`" if (low is not None and high is not None) else ""
    be_note = (
        "\n✅ **Risk-Free Protection Active:** 1/3 profit locked at TP1. Stop-Loss is now moved to **Entry (Breakeven)** for the remaining 2/3 position!"
        if event == "TP1_HIT" and position.get("sl_at_breakeven") else
        "\n✅ **2/3 Profit Locked:** Final 1/3 tranche is riding toward TP3 with Stop-Loss protected at Breakeven!"
        if event == "TP2_HIT" else
        "\n🏆 **Full Trade Completed:** All 3 Take-Profit targets (TP1, TP2, TP3) achieved!"
        if event == "TP3_HIT" else ""
    )
    send_discord({
        "author": {"name": "Future Admiral Research Desk — Live Trade Alert"},
        "title": f"{ticker} — {labels[event]}",
        "description": (
            f"**Direction:** `{position['action']}` · **Entry:** `{_fmt_price(entry)} USDT` · "
            f"**Trigger / Exit:** `{_fmt_price(trigger_px)} USDT` (`{move_pct:+.2f}%`)\n"
            f"**Current Price:** `{_fmt_price(price)} USDT`{rng_str}"
            f"{be_note}"
        ),
        "fields": [
            {"name": "Stop Loss", "value": f"`{_fmt_price(position['sl'])}`" + (" _(Breakeven)_" if position.get("sl_at_breakeven") else ""), "inline": True},
            {"name": "TP1 (1R)", "value": f"`{_fmt_price(position['tp1'])}` · **{position.get('tp1_status', 'PENDING')}**", "inline": True},
            {"name": "TP2 (2R)", "value": f"`{_fmt_price(position['tp2'])}` · **{position.get('tp2_status', 'PENDING')}**", "inline": True},
            {"name": "TP3 (3R)", "value": f"`{_fmt_price(position['tp3'])}` · **{position.get('tp3_status', 'PENDING')}**", "inline": True},
            {"name": "Paper P&L ($100 Notional)", "value": f"**Realized:** `{realized:+.2f} USDT` · **Total:** `{total_pnl:+.2f} USDT` (`{ret_pct:+.2f}%`)", "inline": True},
        ],
        "color": 0xE74C3C if event == "SL_HIT" else 0xF39C12 if event == "BE_EXIT" else 0x95A5A6 if event == "TIME_EXIT" else 0x2ECC71,
        "footer": {"text": f"Source: {position.get('source', 'watchlist')} · {format_pkt(fmt='%Y-%m-%d %H:%M PKT')}"},
    })


_ICON = {"BULLISH": "🟢", "BEARISH": "🔴", "MIXED": "⚪"}


def _tone(macro_ctx):
    return {1: "🟢 RISK-ON (Bullish Tailwind)", -1: "🔴 RISK-OFF (Defensive / Short Bias)", 0: "⚪ NEUTRAL / BALANCED"}.get(
        (macro_ctx or {}).get("risk", 0), "⚪ NEUTRAL / BALANCED"
    )


def build_board(entries, macro_ctx, etf_line):
    stamp = format_pkt(fmt="%Y-%m-%d %H:%M PKT")
    story = "; ".join((macro_ctx or {}).get("story", [])) or "Normal market conditions"
    top = [["MARKET NOW — INSTITUTIONAL DESK (1H Signals + Live Backtest + 30m News)", f"Updated {stamp}"],
           ["Macro & Geopolitics", f"{_tone(macro_ctx)} — " + (" · ".join((macro_ctx or {}).get("notes", [])) or "macro data unavailable") + f"  |  {story}"],
           ["Spot ETF Flows", etf_line],
           [""],
           ["Coin", "Price", "Status — Desk Note", "1D", "4H", "1H", "ADX 4H", "Score", "Updated"]]
    rows = []
    for e in entries:
        tf = (e.get("regime") or {}).get("timeframe_trends", {})
        rows.append([e["ticker"], e.get("price", ""), f"{e['status']} — {e.get('why', '')}", tf.get("1d", ""), tf.get("4h", ""),
                     tf.get("1h", ""), (e.get("regime") or {}).get("adx_4h", ""), e.get("score", ""), stamp])
    return top, rows


_SOFT = ("ADX", "room", "setup", "quality", "chasing")


def _closeness(e):
    if "SIGNAL" in e.get("status", ""):
        return 0
    return 1 if any(w in e.get("why", "") for w in _SOFT) else 2


def _format_coin_row(e):
    coin = e["ticker"].split("-")[0]
    tf = (e.get("regime") or {}).get("timeframe_trends", {})
    t1d = _ICON.get(tf.get("1d"), "⚪")
    t4h = _ICON.get(tf.get("4h"), "⚪")
    t1h = _ICON.get(tf.get("1h"), "⚪")
    price = f"${_fmt_price(e['price'])}" if e.get("price") else "n/a"
    why = e.get("why", "")
    status = e.get("status", "⏳ WAIT")
    if "SIGNAL" in status or "TRADE OPEN" in status:
        return f"**{coin}** `{price}` [{t1d}1D {t4h}4H {t1h}1H] **{status}**\n↳ _{why}_"
    return f"**{coin}** `{price}` [{t1d}1D {t4h}4H {t1h}1H] · _{why[:85]}_"


def briefing_embed(entries, macro_ctx, etf_line):
    ordered = sorted(entries, key=_closeness)
    signals_now = [e for e in ordered if "SIGNAL" in e.get("status", "")]
    open_now = [e for e in ordered if "TRADE OPEN" in e.get("status", "")]
    close_now = [e for e in ordered if _closeness(e) == 1]

    bull_1h = sum(1 for e in entries if (e.get("regime") or {}).get("timeframe_trends", {}).get("1h") == "BULLISH")
    bear_1h = sum(1 for e in entries if (e.get("regime") or {}).get("timeframe_trends", {}).get("1h") == "BEARISH")
    mixed_1h = max(0, len(entries) - bull_1h - bear_1h)

    if signals_now:
        exec_summary = f"⚡ **{len(signals_now)} New Trade Signal(s) Triggered:** " + ", ".join(
            f"**{e['ticker'].split('-')[0]}** ({e['status']})" for e in signals_now
        )
    elif close_now:
        exec_summary = (
            f"🎯 **High-Alert Radar (Setups Forming):** "
            + ", ".join(f"**{e['ticker'].split('-')[0]}**" for e in close_now[:5])
            + " are closest to 1H entry triggers."
        )
    else:
        exec_summary = "🔍 **Market Consolidating:** Coins are mid-range between structure levels; scanner is watching 1H EMA20/VWAP & breakout zones."

    breadth_line = f"📊 **1H Breadth ({len(entries)} pairs):** `🟢 {bull_1h} Bullish` · `🔴 {bear_1h} Bearish` · `⚪ {mixed_1h} Mixed/Range`"
    if open_now:
        breadth_line += f" · `📌 {len(open_now)} Open Trade(s) Monitored`"

    macro_notes = " · ".join((macro_ctx or {}).get("notes", [])) or "Macro series unavailable"
    macro_story = "; ".join((macro_ctx or {}).get("story", [])) or "No major macro headwind"
    macro_val = f"**Regime:** {_tone(macro_ctx)}\n**Drivers:** {macro_story}\n**Tape:** `{macro_notes}`"

    mid = (len(ordered) + 1) // 2
    col1 = "\n".join(_format_coin_row(e) for e in ordered[:mid]) or "-"
    col2 = "\n".join(_format_coin_row(e) for e in ordered[mid:]) or "-"

    fields = [
        {"name": "🌍 Global Macro, CPI/PPI & Geopolitics (30m Live Radar)", "value": macro_val[:1000], "inline": False},
        {"name": "🏦 Institutional Spot ETF Flows", "value": f"`{etf_line}`"[:1000], "inline": False},
        {"name": "📡 Watchlist Desk — Group 1 (Top Priority)", "value": col1[:1020], "inline": False},
    ]
    if col2 and col2 != "-":
        fields.append({"name": "📡 Watchlist Desk — Group 2", "value": col2[:1020], "inline": False})

    return {
        "author": {"name": "Future Admiral Research Desk — Institutional Terminal"},
        "title": "📊 Market briefing (1H Scan + Live Backtest + 30m News Radar)",
        "description": f"{exec_summary}\n{breadth_line}",
        "color": 0x2ECC71 if signals_now else 0x1F6FEB,
        "fields": fields,
        "footer": {"text": f"{format_pkt(fmt='%Y-%m-%d %H:%M PKT')} · 1H Institutional Engine + Live Rolling Backtest"},
    }


def _age_hours(position, now):
    try:
        created = datetime.fromisoformat(str(position.get("created_at_utc")).replace("Z", "+00:00"))
        return (now - created).total_seconds() / 3600
    except (TypeError, ValueError):
        return 0.0


def monitor_positions(positions, prices, state, run_id, health_alerts):
    closed_count = 0
    for signal_id, position in list(positions.items()):
        ticker = position["ticker"]
        cmp = prices.get(ticker) or get_current_price(ticker)[0]
        if not cmp:
            health_alerts.append(f"{ticker}: no price for open position this run; monitor skipped.")
            continue
        candle_range = get_price_range_since(ticker, position.get("last_checked_utc"))
        if not candle_range:
            health_alerts.append(f"{ticker}: SL/TP monitor could not fetch candle range; latest-price-only check used.")
        low = min(candle_range["low"], cmp) if candle_range else cmp
        high = max(candle_range["high"], cmp) if candle_range else cmp
        outcome = evaluate_position_range(position, low, high)
        for stage in outcome["newly_hit"]:
            position[f"tp{stage}_status"] = "HIT"
        apply_paper_outcome(position, outcome, cmp)
        now = datetime.now(timezone.utc)

        if not outcome["closed"] and _age_hours(position, now) >= MAX_HOLD_HOURS:
            direction = 1.0 if position["action"] == "BUY" else -1.0
            remaining = float(position.get("paper_remaining_qty", 0.0) or 0.0)
            position["paper_realized_pnl_usdt"] = float(position.get("paper_realized_pnl_usdt", 0.0) or 0.0) \
                + remaining * (cmp - float(position["entry"])) * direction
            position["paper_remaining_qty"] = 0.0
            mark_to_market(position, cmp)
            outcome = {**outcome, "status": "TIME_EXIT", "closed": True, "exit_price": cmp, "ambiguous": False}

        position["last_checked_utc"] = now.isoformat()
        position["status"] = outcome["status"]
        event_name = outcome["status"] if outcome["newly_hit"] or outcome["closed"] else "PENDING"
        if outcome.get("moved_to_breakeven"):
            reason = "TP1 banked; stop-loss moved to entry (breakeven)."
        elif outcome["status"] == "BE_EXIT":
            reason = "Stopped at breakeven after TP1 was already banked."
        elif outcome["status"] == "TIME_EXIT":
            reason = f"Closed at market after the {MAX_HOLD_HOURS:g}h time limit."
        elif outcome["ambiguous"]:
            reason = "SL and at least one TP crossed in the same observed range; conservatively counted SL first."
        elif not candle_range:
            reason = "Candle range feed unavailable; checked only the latest price."
        else:
            reason = f"Checked {candle_range['source']} since previous scan ({candle_range['candles']} candles)."

        changed = bool(outcome["newly_hit"] or outcome["closed"] or outcome.get("moved_to_breakeven"))
        if changed or SHEET_EVERY_RUN:
            sheet_ok = update_position({
                "run_id": run_id, "signal_id": signal_id, "ticker": ticker,
                "source": position.get("source", "watchlist"), "event": event_name,
                "action": position["action"], "price": cmp, "entry": position.get("entry"),
                "last_price": position.get("last_price", cmp), "created_at_utc": position.get("created_at_utc"),
                "sl": position.get("sl"), "tp1": position.get("tp1"), "tp2": position.get("tp2"), "tp3": position.get("tp3"),
                "tp1_status": position.get("tp1_status", "PENDING"), "tp2_status": position.get("tp2_status", "PENDING"),
                "tp3_status": position.get("tp3_status", "PENDING"), "status": outcome["status"],
                "range_low": low, "range_high": high, "exit_price": outcome["exit_price"],
                "closed_at_utc": now.isoformat() if outcome["closed"] else "",
                "score": position.get("score"), "coverage": position.get("coverage"),
                "risk_basis": position.get("risk_basis", ""), "reason": reason,
                "notes": position.get("notes") or position.get("reason", ""),
                "paper_notional_usdt": position.get("paper_notional_usdt"),
                "paper_initial_qty": position.get("paper_initial_qty"),
                "paper_remaining_qty": position.get("paper_remaining_qty"),
                "paper_realized_pnl_usdt": position.get("paper_realized_pnl_usdt"),
                "paper_unrealized_pnl_usdt": position.get("paper_unrealized_pnl_usdt"),
                "paper_total_pnl_usdt": position.get("paper_total_pnl_usdt"),
                "paper_return_pct": position.get("paper_return_pct"),
                "paper_pnl_method": position.get("paper_pnl_method", ""),
            })
            if not sheet_ok:
                health_alerts.append(f"{ticker}: Google Sheets position update failed or is not configured.")
        for stage in outcome["newly_hit"]:
            _notify_stage(ticker, position, f"TP{stage}_HIT", cmp, low=low, high=high)
        if outcome["closed"]:
            position["exit_price"] = outcome["exit_price"]
            if outcome["status"] in ("SL_HIT", "BE_EXIT", "TIME_EXIT"):
                _notify_stage(ticker, position, outcome["status"], cmp, low=low, high=high)
            engine_state.record_close(state, position, outcome["status"], now)
            del positions[signal_id]
            closed_count += 1
        else:
            positions[signal_id] = position
    return closed_count


def open_position(positions, result, run_id, source="watchlist"):
    signal_id = uuid.uuid4().hex
    created_at = datetime.now(timezone.utc).isoformat()
    matrix = result["matrix"]
    signal = {
        "run_id": run_id, "signal_id": signal_id, "source": source, "ticker": result["ticker"],
        "event": "SIGNAL", "action": result["action"], "price": result["cmp"], "entry": result["cmp"],
        "sl": result["sl"], "tp1": result["tp1"], "tp2": result["tp2"], "tp3": result["tp3"],
        "tp1_status": "PENDING", "tp2_status": "PENDING", "tp3_status": "PENDING", "status": "PENDING",
        "score": matrix["score"], "coverage": matrix["coverage"], "risk_basis": result["risk_basis"],
        "institutional_flow": result.get("institutional_flow") or {}, "market_regime": matrix["market_regime"], "orderflow_proxies": matrix["orderflow_proxies"],
        "setup": result["setup"], "grade": result["grade"],
        "reason": f"{result['setup']} setup, grade {result['grade']} ({result['quality']:.0f}/100): " + " · ".join(result["reasons"][:4]),
        "notes": f"{result['setup']} setup, grade {result['grade']} ({result['quality']:.0f}/100): " + " · ".join(result["reasons"][:4]),
    }
    signal.update(paper_signal_fields(result["cmp"], result["action"]))
    positions[signal_id] = {**signal, "created_at_utc": created_at, "last_checked_utc": created_at, "tp": result["tp1"]}
    return signal_id, signal


def daily_heartbeat(state, positions, prices, feed_failures, total, ctx=None):
    today = now_pkt().strftime("%Y-%m-%d")
    if now_pkt().hour < HEARTBEAT_HOUR_PKT or state.get("last_heartbeat_date") == today:
        return
    stats = engine_state.recent_stats(7)
    lines = []
    for p in positions.values():
        px = prices.get(p["ticker"])
        move = ((px - p["entry"]) / p["entry"] * 100 * (1 if p["action"] == "BUY" else -1)) if px else None
        lines.append(f"• **{p['ticker']}** `{p['action']}` @ `{_fmt_price(p['entry'])}`"
                     + (f" (`{move:+.2f}%`)" if move is not None else "") + f" · `{p.get('status', 'PENDING')}`")
    until = engine_state.paused_until(state, datetime.now(timezone.utc))
    macro_ctx = (ctx or {}).get("macro") or {}
    brief = None
    try:
        import llm_verdict, macro as macro_mod
        headlines = macro_ctx.get("headlines") or macro_mod.get_headlines()
        brief = llm_verdict.get_macro_brief(macro_ctx.get("notes", []), headlines)
    except Exception as exc:
        print(f"[!] macro brief skipped: {str(exc)[:80]}")
    send_discord({
        "author": {"name": "Future Admiral Research Desk — Daily Operations"}, "title": "Daily status — scanner alive ✅",
        "description": f"1H institutional scans + live rolling backtest + 30m global news checks are running smoothly. `{total - feed_failures}/{total}` coins had clean data this run.",
        "color": 0x2ECC71,
        "fields": [
            {"name": "📌 Active Open Positions", "value": "\n".join(lines) or "No open positions right now.", "inline": False},
            {"name": "📈 Last 7 Days Performance (Paper, Gross)", "value": f"`{stats['trades']} closed` · `{stats['wins']} winners` · `{stats['total_r']:+.2f}R net`", "inline": False},
            {"name": "🌍 Macro & Geopolitics Desk", "value": (brief or "Rule-based macro radar active") + "\n`" + (" · ".join(macro_ctx.get("notes", [])) or "no macro data") + "`", "inline": False},
            {"name": "🛡️ Circuit Breaker Status", "value": f"Paused until `{until.strftime('%Y-%m-%d %H:%M UTC')}`" if until else "Active & Ready (No pause)", "inline": False},
        ],
        "footer": {"text": format_pkt(fmt="%Y-%m-%d %H:%M PKT")},
    })
    state["last_heartbeat_date"] = today


def main():
    t0 = time.time()
    print(f"[*] Admiral v4.0 1H/30m scan at {format_pkt()}")
    prepare_sheet()
    coins = load_watchlist()
    positions = load_positions()
    positions = sync_open_positions(positions)
    state = engine_state.load_state()
    state = load_cloud_state(state)
    state.setdefault("last_signal_candle_ts", {})
    run_id = uuid.uuid4().hex
    health_alerts = []

    watch = [c["ticker"] for c in coins]
    tickers = list(dict.fromkeys(watch + [p["ticker"] for p in positions.values()]))
    md.clear_cache()
    try:
        prices = md.get_prices(tickers)
    except Exception as exc:
        prices = {}
        health_alerts.append(f"Bulk price request failed: {str(exc)[:120]}")
    for t in tickers:
        if t not in prices:
            px, _ = get_current_price(t)
            if px:
                prices[t] = px

    closed = monitor_positions(positions, prices, state, run_id, health_alerts)
    save_positions(positions)

    if not scan_window_open():
        print("[·] Outside the 4H-close window: positions monitored, no new-signal scan this hour.")
        save_positions(positions)
        engine_state.save_state(state)
        save_cloud_state(state)
        print(f"[*] Done in {time.time() - t0:.0f}s · open {len(positions)} · closed {closed}")
        return
    ctx = load_context(state=state)
    for problem in (ctx.get("macro") or {}).get("errors", [])[:6]:
        health_alerts.append(f"macro: {problem}")
    feed_failures, candidates, entries = 0, [], []
    for ticker in watch:
        if ticker not in prices:
            feed_failures += 1
            print(f"[!] {ticker}: no price this run")
            entries.append({"ticker": ticker, "status": "⚠️ NO DATA", "why": "price feed unavailable"})
            continue
        open_pos = next((p for p in positions.values() if p.get("ticker") == ticker), None)
        if open_pos is not None:
            print(f"[Skip] {ticker}: open position is being monitored.")
            p_act = open_pos.get("action", "")
            p_ent = open_pos.get("entry")
            p_st = open_pos.get("status", "PENDING")
            p_pnl = float(open_pos.get("paper_total_pnl_usdt", 0.0) or 0.0)
            p_sc = open_pos.get("score")
            sc_100 = round(abs(float(p_sc)) * 100.0, 1) if isinstance(p_sc, (int, float)) and abs(float(p_sc)) <= 1.5 else (p_sc or "")
            ent_str = f" @ {_fmt_price(p_ent)}" if p_ent else ""
            entries.append({
                "ticker": ticker,
                "price": prices[ticker],
                "regime": open_pos.get("market_regime") or {},
                "score": sc_100,
                "status": f"📌 TRADE OPEN ({p_act})",
                "why": f"{p_act}{ent_str} · {p_st} · P&L {p_pnl:+.2f} USDT (SL/TP monitored live)",
            })
            continue
        result = analyze_ticker(ticker, current_price=prices[ticker], ctx=ctx)
        if not result["ok"]:
            feed_failures += 1
            health_alerts.append(f"{ticker}: {result['error']}")
            entries.append({"ticker": ticker, "price": prices.get(ticker), "status": "⚠️ NO DATA", "why": str(result["error"])[:80]})
            continue
        entries.append({"ticker": ticker, "price": result["cmp"], "regime": result["market_regime"],
                        "score": result["quality"] or "", "result": result,
                        "status": ("🟢 BUY SIGNAL" if result["action"] == "BUY" else "🔴 SELL SIGNAL") if result["action"] in ("BUY", "SELL") else "⏳ WAIT",
                        "why": (" · ".join(result["reasons"][:2]) if result["action"] in ("BUY", "SELL")
                                else (result["blockers"][0] if result["blockers"] else "Awaiting setup"))})
        if result["data_health"]:
            record_health(result["data_health"], ticker=ticker, run_id=run_id, source="watchlist")
        if result["action"] in ("BUY", "SELL"):
            candidates.append(result)
        else:
            print(f"[·] {ticker}: no signal — {result['no_trade_reason'] or 'no setup'}")
            if LOG_NO_SIGNAL:
                record_scan_event({"run_id": run_id, "ticker": ticker, "source": "watchlist", "event": "NO_SIGNAL",
                                   "action": "NO SIGNAL", "price": result["cmp"], "score": result["matrix"]["score"],
                                   "coverage": result["matrix"]["coverage"], "market_regime": result["market_regime"],
                                   "reason": result["no_trade_reason"]})

    new_signals = []
    now = datetime.now(timezone.utc)
    for result in sorted(candidates, key=lambda r: -r["quality"]):
        ticker = result["ticker"]
        bar_ts = result.get("bar_ts")
        if bar_ts and state["last_signal_candle_ts"].get(ticker) == bar_ts:
            print(f"[Skip] {ticker}: signal already issued on this 1H candle ({bar_ts}).")
            for e in entries:
                if e["ticker"] == ticker:
                    e["status"], e["why"] = "⏳ WAIT", "Signal already issued on this 1H candle"
            continue
        ok, why = engine_state.portfolio_gate(result["action"], ticker, positions, state, now)
        if not ok:
            print(f"[Gate] {ticker} {result['action']} ({result['quality']:.0f}/100) skipped: {why}")
            for e in entries:
                if e["ticker"] == ticker:
                    e["status"], e["why"] = "⏸️ SETUP (GATED)", why[:80]
            continue
        add_verdict(result)
        signal_id, signal = open_position(positions, result, run_id)
        if bar_ts:
            state["last_signal_candle_ts"][ticker] = bar_ts
        if not record_scan_event(signal):
            health_alerts.append(f"{ticker}: Google Sheets signal logging failed or is not configured.")
        send_discord(result["embed"])
        save_positions(positions)
        new_signals.append((ticker, result["action"]))
        print(f"[✓] {ticker} {result['action']} grade {result['grade']} ({result['quality']:.0f}/100) entry {result['cmp']}")

    try:
        _, etf_raw = etf_signal("BTC-USDT")
        etf_line = etf_flow.format_institutional_flow(etf_raw) if etf_raw else "ETF data unavailable"
        top, board_rows = build_board(entries, ctx.get("macro"), etf_line)
        write_board(top, board_rows)
        slot = f"{now.strftime('%Y-%m-%d')}-{now.hour // 4}"
        if state.get("last_briefing_slot") != slot:
            if send_discord(briefing_embed(entries, ctx.get("macro"), etf_line)):
                state["last_briefing_slot"] = slot
    except Exception as exc:
        print(f"[!] board/briefing skipped: {type(exc).__name__} {str(exc)[:100]}")

    if state.pop("just_paused", False):
        until = engine_state.paused_until(state, now)
        send_discord({"title": "⏸️ Circuit breaker — new signals paused",
                      "description": f"{engine_state.PAUSE_AFTER_LOSSES} stop-losses in a row. No new signals until "
                                     f"{until.strftime('%Y-%m-%d %H:%M UTC') if until else 'the pause ends'}; open positions are still tracked.",
                      "color": 0xF39C12})

    if watch and feed_failures >= max(2, len(watch) // 2):
        last = engine_state._parse(state.get("last_feed_alert_utc"))
        if not last or (now - last).total_seconds() > 6 * 3600:
            send_discord({"title": "⚠️ Data feed problem",
                          "description": f"{feed_failures}/{len(watch)} coins had no usable OKX data this run. "
                                         "Existing positions keep their last known state; scanning resumes automatically.",
                          "color": 0xE74C3C})
            state["last_feed_alert_utc"] = now.isoformat()
        record_health_event({"run_id": run_id, "ticker": "", "source": "watchlist", "category": "FEED_ERRORS",
                             "messages": list(dict.fromkeys(health_alerts))[:10]})
    daily_heartbeat(state, positions, prices, feed_failures, len(watch), ctx)

    for item in dict.fromkeys(health_alerts):
        print(f"::warning title=Future Admiral data health::{item}")
    if health_alerts:
        record_health(list(dict.fromkeys(health_alerts)), run_id=run_id, source="watchlist")
    save_positions(positions)
    engine_state.save_state(state)
    save_cloud_state(state)
    print(f"[*] Done in {time.time() - t0:.0f}s · open {len(positions)} · closed {closed} · new {len(new_signals)} · "
          f"feed problems {feed_failures}/{len(watch)}")


if __name__ == "__main__":
    main()

"""
analyze_now.py (v4.0) - on-demand check of ANY coin, right now.
"""
import os
import sys
import uuid

import engine_state
from admiral_core import add_verdict, analyze_ticker, discord_failure_summary, load_context, send_discord
from admiral_utils import format_pkt, normalize_ticker
from cloud_run import open_position
from google_sheets_sync import prepare as prepare_sheet, record_scan_event, sync_open_positions
from health_log import record_health
from position_state import load_positions, save_positions


def main():
    if len(sys.argv) < 2 or not sys.argv[1].strip():
        print("Usage: python analyze_now.py <COIN>   e.g. python analyze_now.py SOL-USDT")
        sys.exit(1)
    ticker = normalize_ticker(sys.argv[1])
    print(f"[*] On-demand analysis requested for {ticker} at {format_pkt()}")
    prepare_sheet()
    run_id = uuid.uuid4().hex
    label = "Future Admiral — On-Demand Analysis"

    result = analyze_ticker(ticker, source_label=label, ctx=load_context([ticker]))
    if result.get("data_health"):
        record_health(result["data_health"], ticker=ticker, run_id=run_id, source="manual")
    if not result["ok"]:
        print(f"[!] Analysis failed for {ticker}: {result['error']}")
        send_discord({"title": f"{ticker} — On-Demand Analysis Failed", "description": discord_failure_summary(result["error"]),
                      "color": 0xFF0000, "footer": {"text": f"Admiral On-Demand • {format_pkt(fmt='%Y-%m-%d %H:%M PKT')}"}})
        github_output = os.getenv("GITHUB_OUTPUT", "").strip()
        if github_output:
            with open(github_output, "a", encoding="utf-8") as stream:
                stream.write("analysis_failed=true\n")
        sys.exit(1)

    positions = sync_open_positions(load_positions())
    already_open = any(p.get("ticker") == ticker for p in positions.values())
    if result["action"] in ("BUY", "SELL") and not already_open:
        add_verdict(result, label)
        _, signal = open_position(positions, result, run_id, source="manual")
        save_positions(positions)
        record_scan_event(signal)
        print(f"[✓] {ticker}: {result['action']} grade {result['grade']} entry={result['cmp']} sl={result['sl']} tp1={result['tp1']}")
        print("    (saved as a persistent position; the hourly scanner tracks its SL/TP)")
    elif result["action"] in ("BUY", "SELL"):
        print(f"[·] {ticker}: {result['action']} setup found but a position is already open; not duplicating.")
    else:
        print(f"[·] {ticker}: NO SIGNAL — {result['no_trade_reason'] or 'no setup'}")
    send_discord(result["embed"])
    print("[✓] Posted to Discord.")


if __name__ == "__main__":
    main()

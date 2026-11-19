"""Append detailed diagnostics locally, away from user-facing Discord alerts."""

import json
from pathlib import Path

from admiral_utils import format_pkt

HEALTH_LOG = Path("data_health.log")


def record_health(messages, *, ticker="", run_id="", source=""):
    entries = [str(item).strip() for item in messages if item]
    if not entries:
        return
    record = {
        "time_pkt": format_pkt(),
        "run_id": run_id,
        "ticker": ticker,
        "source": source,
        "diagnostics": entries,
    }
    try:
        with HEALTH_LOG.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        print(f"[!] Could not write {HEALTH_LOG}: {exc}")

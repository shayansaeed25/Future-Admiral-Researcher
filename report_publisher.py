from __future__ import annotations

import os
from pathlib import Path


def publish_report(report_path: str | Path | None) -> bool:
    """Validate and queue a report for the workflow's single final push.

    Pushing once per coin caused redundant Git operations and flaky report
    publishing. The final Actions step commits all reports and state together.
    """
    if os.getenv("GITHUB_ACTIONS", "").lower() != "true":
        return True
    if not report_path:
        return False

    report_dir = Path(report_path).parent
    if not report_dir.is_dir():
        print(f"[!] Cannot publish report: directory is missing: {report_dir}")
        return False

    if os.getenv("GITHUB_ACTIONS", "").lower() == "true":
        print(f"[*] Report queued for the scan's final commit: {report_dir}")
    return True

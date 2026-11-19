"""Capture warnings from data providers during one analysis run."""
from __future__ import annotations

import logging


class DataHealthCapture(logging.Handler):
    """Collect provider warnings so they can appear in the signal and alerts."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        name = record.name.lower()
        message = " ".join(record.getMessage().split())
        if not message:
            return
        data_source_warning = any(token in message.lower() for token in (
            "fetch", "feed", "unavailable", "rate limit", "429", "403", "404",
            "timeout", "vendor", "no usable data", "api error", "http error",
        ))
        if not (name.startswith("tradingagents.dataflows") or name.startswith("yfinance") or data_source_warning):
            return
        item = f"{record.name}: {message}"[:240]
        if item not in self.messages:
            self.messages.append(item)


def summarize_data_health(events: list[str], limit: int = 12) -> list[str]:
    """Deduplicate and cap per-run health events for Discord/Sheets limits."""
    unique = list(dict.fromkeys(" ".join(str(item).split()) for item in events if item))
    if len(unique) > limit:
        unique = unique[:limit] + [f"And {len(unique) - limit} additional data warning(s); see Actions logs."]
    return unique

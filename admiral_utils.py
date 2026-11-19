"""Tiny dependency-free helpers (symbol normalisation + Pakistan time).

Replaces the imports from the heavy ``tradingagents`` package so the hourly
bot only needs ``requests``, ``numpy`` and ``pytz``.
"""
from __future__ import annotations

import re
from datetime import datetime

import pytz

PKT = pytz.timezone("Asia/Karachi")
_QUOTES = ("USDT", "USDC", "USD")


def now_pkt() -> datetime:
    return datetime.now(PKT)


def format_pkt(value: datetime | None = None, fmt: str = "%Y-%m-%d %H:%M:%S PKT") -> str:
    value = value or now_pkt()
    if value.tzinfo is None:
        value = pytz.UTC.localize(value)
    return value.astimezone(PKT).strftime(fmt)


def crypto_base(raw) -> str | None:
    """``BTC-USDT`` / ``btcusdt`` / ``BTC/USD`` -> ``BTC``; None if not a pair."""
    if not isinstance(raw, str):
        return None
    text = raw.strip().upper().rstrip("+")
    for sep in ("-", "/"):
        if sep in text:
            base, quote = text.rsplit(sep, 1)
            if quote in _QUOTES and re.fullmatch(r"[A-Z0-9]{2,20}", base):
                return base
    compact = text.replace("-", "").replace("/", "")
    for quote in _QUOTES:
        if compact.endswith(quote) and re.fullmatch(r"[A-Z0-9]{2,20}", compact[: -len(quote)]):
            return compact[: -len(quote)]
    return None


def normalize_ticker(raw: str) -> str:
    """Bare ``sol`` / ``SOLUSDT`` / ``SOL-USD`` -> ``SOL-USDT``."""
    text = (raw or "").strip().upper()
    base = crypto_base(text)
    if not base:
        base = re.sub(r"[^A-Z0-9]", "", text)
    return f"{base}-USDT" if base else ""

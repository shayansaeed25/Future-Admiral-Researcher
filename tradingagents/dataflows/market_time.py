"""Pakistan Standard Time helpers for user-visible timestamps."""

from datetime import datetime

import pytz

PKT = pytz.timezone("Asia/Karachi")


def now_pkt() -> datetime:
    return datetime.now(PKT)


def format_pkt(value: datetime | None = None, fmt: str = "%Y-%m-%d %H:%M:%S PKT") -> str:
    value = value or now_pkt()
    if value.tzinfo is None:
        value = pytz.UTC.localize(value)
    return value.astimezone(PKT).strftime(fmt)

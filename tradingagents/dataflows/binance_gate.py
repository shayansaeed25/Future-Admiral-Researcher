"""Process-wide short-circuit for a geo-blocked Binance region.

The GitHub Actions runner's region gets HTTP 451 from Binance on every call
(price, klines, futures data - all of it). Without this, every analyst/coin
in a run independently retries Binance, times out or gets 451, then falls
back to Bybit/OKX - burning several seconds per call, dozens of times per
run, for a failure that is already known after the very first attempt.

Once any call in this process observes a 451, this flips a flag so every
later call in the same run skips Binance immediately and goes straight to
the fallback venues. It resets on the next process start (each scheduled
run / GitHub Actions job is a fresh process), so a temporary regional block
never permanently disables a working Binance feed.
"""

_blocked = False


def is_binance_blocked() -> bool:
    return _blocked


def mark_binance_blocked() -> None:
    global _blocked
    _blocked = True


def mark_binance_ok() -> None:
    """Call on a confirmed successful Binance response, in case an earlier
    attempt in this run mis-flagged a transient error as a geo-block."""
    global _blocked
    _blocked = False


def note_binance_response(exc: Exception | None = None, status_code: int | None = None) -> None:
    """Inspect a completed Binance HTTP attempt and flip the breaker on 451.

    Pass either the caught exception (its .response.status_code is read if
    present) or the status_code directly.
    """
    code = status_code
    if code is None and exc is not None:
        response = getattr(exc, "response", None)
        code = getattr(response, "status_code", None)
    if code == 451:
        mark_binance_blocked()

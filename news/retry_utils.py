from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional


def bounded_backoff_seconds(
    attempt_number: int,
    *,
    base_seconds: float,
    max_seconds: float,
) -> float:
    """
    Deterministic exponential backoff without jitter.

    `attempt_number` is 1-based and represents the failed attempt after which
    the caller is about to sleep before trying again.
    """
    if attempt_number <= 0:
        raise ValueError(
            "attempt_number must be > 0."
        )

    if base_seconds < 0:
        raise ValueError(
            "base_seconds must be >= 0."
        )

    if max_seconds < 0:
        raise ValueError(
            "max_seconds must be >= 0."
        )

    delay = float(base_seconds) * (
        2 ** (attempt_number - 1)
    )

    return round(
        min(
            float(max_seconds),
            delay,
        ),
        6,
    )


def retry_after_iso(
    now: datetime,
    *,
    cooldown_minutes: int,
) -> Optional[str]:
    if cooldown_minutes < 0:
        raise ValueError(
            "cooldown_minutes must be >= 0."
        )

    if cooldown_minutes == 0:
        return now.isoformat()

    return (
        now
        + timedelta(
            minutes=cooldown_minutes
        )
    ).isoformat()
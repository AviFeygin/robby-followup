"""Time helpers. All stored times are UTC text like 2026-08-16T16:00:00Z."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

UTC = timezone.utc


def parse(value: str | datetime) -> datetime:
    """Parse an ISO timestamp to an aware UTC datetime. Naive input is taken as UTC."""
    if isinstance(value, datetime):
        dt = value
    else:
        text = value.strip()
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def hours_between(earlier: datetime, later: datetime) -> float:
    return (later - earlier).total_seconds() / 3600.0


def add_hours(dt: datetime, hours: float) -> datetime:
    return dt + timedelta(hours=hours)

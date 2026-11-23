"""Limits that are about time and volume."""
from __future__ import annotations

from datetime import datetime, timedelta

from ..platform.config import ShopConfig


def local_hour(now: datetime, config: ShopConfig) -> int:
    return (now + timedelta(hours=config.utc_offset_hours)).hour


def in_quiet_hours(now: datetime, config: ShopConfig) -> bool:
    """True when the shop should not be texting customers."""
    if not config.quiet_hours_enabled:
        return False
    hour = local_hour(now, config)
    return not (config.quiet_start_hour <= hour < config.quiet_end_hour)

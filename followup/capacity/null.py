"""Used when no schedule is connected. The policy then behaves as if capacity were normal."""
from __future__ import annotations

from datetime import datetime

from .base import CapacitySnapshot


class NullCapacity:
    def snapshot(self, shop_id: str, now: datetime) -> CapacitySnapshot:
        return CapacitySnapshot()

    def slot_open(self, shop_id: str, slot_id: str, now: datetime) -> bool:
        return False

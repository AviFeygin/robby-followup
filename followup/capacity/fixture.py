"""A schedule held in memory, per shop. Used by tests and by the demo's capacity view.

`near_customer_ids` stands in for the travel time calculation the real provider does.
"""
from __future__ import annotations

from datetime import datetime

from ..platform.timeutil import parse
from .base import CapacitySnapshot, Opening


class FixtureCapacity:
    def __init__(self) -> None:
        self._shops: dict[str, CapacitySnapshot] = {}
        self.fail = False            # simulate the schedule service being down

    def set(self, shop_id: str, booked_hours: float | None = None, median_booked_hours: float | None = None,
            available_hours: float | None = None, openings: list[dict] | None = None) -> None:
        self._shops[shop_id] = CapacitySnapshot(
            booked_hours=booked_hours,
            median_booked_hours=median_booked_hours,
            available_hours=available_hours,
            openings=tuple(
                Opening(o["slot_id"], o["tech_name"], o["label"], o["ends_at"], tuple(o.get("near_customer_ids", ())))
                for o in (openings or [])
            ),
        )

    def clear(self, shop_id: str) -> None:
        self._shops.pop(shop_id, None)

    def fill_slot(self, shop_id: str, slot_id: str) -> None:
        snap = self._shops.get(shop_id)
        if snap:
            self._shops[shop_id] = CapacitySnapshot(
                snap.booked_hours, snap.median_booked_hours, snap.available_hours,
                tuple(o for o in snap.openings if o.slot_id != slot_id),
            )

    def snapshot(self, shop_id: str, now: datetime) -> CapacitySnapshot:
        if self.fail:
            raise ConnectionError("schedule service unavailable")
        snap = self._shops.get(shop_id, CapacitySnapshot())
        live = tuple(o for o in snap.openings if parse(o.ends_at) > now)
        return CapacitySnapshot(snap.booked_hours, snap.median_booked_hours, snap.available_hours, live)

    def slot_open(self, shop_id: str, slot_id: str, now: datetime) -> bool:
        if self.fail:
            raise ConnectionError("schedule service unavailable")
        return any(o.slot_id == slot_id for o in self.snapshot(shop_id, now).openings)

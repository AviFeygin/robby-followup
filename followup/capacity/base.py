"""Capacity interface.

The real provider reads the scheduling system and works out travel times. The engine
only needs the two answers below, so that is all the interface exposes. Job addresses
and the other customer's name never cross this boundary.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class Opening:
    slot_id: str
    tech_name: str
    label: str                       # customer safe text, such as "Thursday afternoon"
    ends_at: str                     # ISO time the slot stops being bookable
    near_customer_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class CapacitySnapshot:
    booked_hours: float | None = None          # coming week
    median_booked_hours: float | None = None   # the shop's own median for a week
    available_hours: float | None = None
    openings: tuple[Opening, ...] = ()

    @property
    def connected(self) -> bool:
        return self.booked_hours is not None and bool(self.median_booked_hours)

    @property
    def shop_slack(self) -> float:
        """0 when the shop is at or above its usual load, 1 when the week is empty."""
        if not self.connected:
            return 0.0
        slack = (self.median_booked_hours - self.booked_hours) / self.median_booked_hours
        return max(0.0, min(1.0, slack))

    @property
    def fully_booked(self) -> bool:
        return (
            self.booked_hours is not None
            and self.available_hours is not None
            and self.booked_hours >= self.available_hours
        )

    def opening_near(self, customer_id: str) -> Opening | None:
        for o in self.openings:
            if customer_id in o.near_customer_ids:
                return o
        return None

    def to_dict(self) -> dict:
        return {
            "connected": self.connected,
            "booked_hours": self.booked_hours,
            "median_booked_hours": self.median_booked_hours,
            "available_hours": self.available_hours,
            "shop_slack": round(self.shop_slack, 2),
            "fully_booked": self.fully_booked,
            "openings": [
                {"slot_id": o.slot_id, "tech_name": o.tech_name, "label": o.label, "ends_at": o.ends_at,
                 "near_customer_ids": list(o.near_customer_ids)}
                for o in self.openings
            ],
        }


class CapacityProvider(Protocol):
    def snapshot(self, shop_id: str, now: datetime) -> CapacitySnapshot: ...

    def slot_open(self, shop_id: str, slot_id: str, now: datetime) -> bool: ...

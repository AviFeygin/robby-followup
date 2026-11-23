"""The event log. Append only. The primary key makes a duplicate append a no-op."""
from __future__ import annotations

import json
from typing import Any

from .db import ShopSession


def append(session: ShopSession, event: dict[str, Any]) -> bool:
    """Append one normalised event. Returns False if that event id was already stored."""
    changed = session.run(
        "INSERT INTO events (shop_id, event_id, quote_id, type, occurred_at, channel, direction, raw_json) "
        "VALUES (:shop_id, :event_id, :quote_id, :type, :occurred_at, :channel, :direction, :raw_json) "
        "ON CONFLICT DO NOTHING",
        event_id=event["event_id"],
        quote_id=event.get("quote_id"),
        type=event["type"],
        occurred_at=event["occurred_at"],
        channel=event.get("channel"),
        direction=event.get("direction"),
        raw_json=json.dumps(event.get("raw", event), sort_keys=True),
    )
    return changed == 1


def read(session: ShopSession) -> list[dict[str, Any]]:
    return session.rows(
        "SELECT event_id, quote_id, type, occurred_at, channel, direction FROM events "
        "WHERE shop_id = :shop_id ORDER BY occurred_at, event_id"
    )

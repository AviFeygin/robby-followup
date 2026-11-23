"""Fold events into per quote state.

Every operation here is order independent: sets, sorted lists, maxima. Shuffled or
duplicated input therefore gives the same state. Nothing reads arrival order.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable

from ..platform.timeutil import parse


@dataclass
class QuoteState:
    quote_id: str
    customer_id: str
    customer_name: str
    tech_name: str
    amount_cents: int
    created_at: datetime
    status: str                      # open, accepted, dismissed
    views: list[datetime] = field(default_factory=list)
    replies: list[tuple[datetime, str | None]] = field(default_factory=list)
    contacts: list[datetime] = field(default_factory=list)   # every outbound touch we know of
    accepted_at: datetime | None = None

    @property
    def closed(self) -> bool:
        return self.status != "open"

    @property
    def last_contact(self) -> datetime | None:
        return self.contacts[-1] if self.contacts else None

    @property
    def last_reply(self) -> datetime | None:
        return self.replies[-1][0] if self.replies else None

    @property
    def reply_channel(self) -> str | None:
        return self.replies[-1][1] if self.replies else None

    @property
    def last_view(self) -> datetime | None:
        return self.views[-1] if self.views else None


def project(quotes: Iterable[dict[str, Any]], events: Iterable[dict[str, Any]], now: datetime) -> dict[str, QuoteState]:
    """Build state for every quote that exists at `now`. Events later than `now` are ignored."""
    states: dict[str, QuoteState] = {}
    field_contacts: dict[str, datetime] = {}
    for q in quotes:
        created = parse(q["created_at"])
        if created > now:
            continue
        states[q["id"]] = QuoteState(
            quote_id=q["id"],
            customer_id=q["customer_id"],
            customer_name=q.get("customer_name", ""),
            tech_name=q.get("tech_name", ""),
            amount_cents=int(q["amount_cents"]),
            created_at=created,
            status=q.get("status", "open"),
        )
        if q.get("last_contact_at"):
            stamp = parse(q["last_contact_at"])
            if stamp <= now:
                field_contacts[q["id"]] = stamp

    views: dict[str, set[datetime]] = {}
    replies: dict[str, set[tuple[datetime, str | None]]] = {}
    contacts: dict[str, set[datetime]] = {qid: {stamp} for qid, stamp in field_contacts.items()}
    accepted: dict[str, datetime] = {}

    seen: set[str] = set()
    for e in events:
        if e["event_id"] in seen:      # the log already dedupes, this keeps the fold safe alone
            continue
        seen.add(e["event_id"])
        qid = e.get("quote_id")
        if qid not in states:
            continue
        at = parse(e["occurred_at"])
        if at > now:
            continue
        kind = e["type"]
        if kind == "quote_viewed":
            views.setdefault(qid, set()).add(at)
        elif kind == "customer_replied":
            replies.setdefault(qid, set()).add((at, e.get("channel")))
        elif kind == "message_sent":
            if (e.get("direction") or "outbound") == "outbound":
                contacts.setdefault(qid, set()).add(at)
            else:
                replies.setdefault(qid, set()).add((at, e.get("channel")))
        elif kind == "quote_accepted":
            accepted[qid] = min(at, accepted.get(qid, at))
        # quote_sent is the quote going out, not a follow-up. Unknown types are ignored.

    for qid, state in states.items():
        state.views = sorted(views.get(qid, ()))
        state.replies = sorted(replies.get(qid, ()), key=lambda r: (r[0], r[1] or ""))
        state.contacts = sorted(contacts.get(qid, ()))
        if qid in accepted:
            state.accepted_at = accepted[qid]
            # Closed is sticky. An event can close a quote, nothing reopens it.
            if state.status == "open":
                state.status = "accepted"
    return states

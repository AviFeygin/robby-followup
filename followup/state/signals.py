"""Signals for one quote, computed from its state and a set of time windows."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime

from ..platform.timeutil import hours_between, iso
from .projector import QuoteState


@dataclass(frozen=True)
class Windows:
    """Time windows in hours. Built from the shop's baselines by the policy module."""
    hot_window: float
    burst_window: float
    burst_views: int
    returned_gap: float
    never_viewed_after: float
    fresh_hold: float
    live_conversation_hold: float
    median_amount_cents: float


@dataclass(frozen=True)
class Signals:
    awaiting_our_reply: bool
    viewed_since_contact: bool
    views_since_contact: int
    view_burst: bool
    returned_after_gap: bool
    never_viewed: bool
    never_contacted: bool
    unanswered_attempts: int
    hours_since_contact: float
    age_hours: float
    amount_ratio: float
    reply_channel: str | None
    last_view: str | None
    hold: str | None                 # fresh, live_conversation, or None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["hours_since_contact"] = round(self.hours_since_contact, 1)
        d["age_hours"] = round(self.age_hours, 1)
        d["amount_ratio"] = round(self.amount_ratio, 2)
        return d


def compute_signals(s: QuoteState, now: datetime, w: Windows) -> Signals:
    last_contact = s.last_contact
    last_reply = s.last_reply
    since = last_contact or s.created_at

    awaiting = last_reply is not None and (last_contact is None or last_reply > last_contact)

    views_since = [v for v in s.views if v > since] if last_contact else list(s.views)
    last_view = s.last_view
    hot = bool(views_since) and hours_between(views_since[-1], now) <= w.hot_window

    burst = False
    for i in range(len(s.views) - w.burst_views + 1):
        chunk = s.views[i:i + w.burst_views]
        if hours_between(chunk[0], chunk[-1]) <= w.burst_window and (last_contact is None or chunk[-1] > last_contact):
            burst = True

    returned = False
    if last_view and hours_between(last_view, now) <= w.hot_window:
        earlier = [v for v in s.views if v < last_view]
        previous = earlier[-1] if earlier else s.created_at
        returned = hours_between(previous, last_view) > w.returned_gap

    age = hours_between(s.created_at, now)
    never_viewed = not s.views and age >= w.never_viewed_after

    attempts = len([c for c in s.contacts if last_reply is None or c > last_reply])

    hold = None
    if age < w.fresh_hold:
        hold = "fresh"
    elif last_reply and last_contact and last_contact > last_reply \
            and hours_between(last_contact, now) < w.live_conversation_hold:
        hold = "live_conversation"

    return Signals(
        awaiting_our_reply=awaiting,
        viewed_since_contact=hot,
        views_since_contact=len(views_since),
        view_burst=burst,
        returned_after_gap=returned,
        never_viewed=never_viewed,
        never_contacted=last_contact is None,
        unanswered_attempts=attempts,
        hours_since_contact=hours_between(since, now),
        age_hours=age,
        amount_ratio=(s.amount_cents / w.median_amount_cents) if w.median_amount_cents else 0.0,
        reply_channel=s.reply_channel,
        last_view=iso(last_view) if last_view else None,
        hold=hold,
    )

"""Checks run inside the send transaction, after the draft is claimed. Pure.

A draft can sit for hours between approval and send. Everything is checked again here.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ..platform.timeutil import hours_between


@dataclass(frozen=True)
class SendFacts:
    now: datetime
    quote_status: str
    customer_last_contact: datetime | None     # across all of the customer's quotes
    customer_last_reply: datetime | None
    cooldown_hours: float
    body_hash: str
    approved_body_hash: str | None
    decided_by: str | None
    offer_expires_at: datetime | None = None
    slot_ref: str | None = None
    slot_open: bool = True
    shop_sending_enabled: bool = True
    global_sending_enabled: bool = True
    sent_last_24h: int = 0
    daily_send_cap: int = 50
    in_quiet_hours: bool = False


@dataclass(frozen=True)
class Verdict:
    action: str        # ok, skip, refuse, defer
    rule: str = ""
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.action == "ok"

    def to_dict(self) -> dict:
        return {"action": self.action, "rule": self.rule, "reason": self.reason}


def is_human(actor: str | None) -> bool:
    return bool(actor) and actor.startswith("user:")


def check_send(f: SendFacts) -> Verdict:
    # Kill switches first. Nothing leaves while they are off.
    if not f.global_sending_enabled:
        return Verdict("refuse", "kill_switch", "sending is switched off for the platform")
    if not f.shop_sending_enabled:
        return Verdict("refuse", "kill_switch", "sending is switched off for this shop")

    # Approval. A human approved exactly this text.
    if not is_human(f.decided_by) or not f.approved_body_hash:
        return Verdict("refuse", "no_approval", "no human approval on record")
    if f.body_hash != f.approved_body_hash:
        return Verdict("refuse", "hash_mismatch", "text changed after approval")

    # The assignment's guardrails.
    if f.quote_status != "open":
        return Verdict("skip", "closed_quote", f"quote is {f.quote_status}")
    if f.customer_last_contact is not None:
        replied_since = f.customer_last_reply is not None and f.customer_last_reply > f.customer_last_contact
        if not replied_since and hours_between(f.customer_last_contact, f.now) < f.cooldown_hours:
            waited = hours_between(f.customer_last_contact, f.now)
            return Verdict("skip", "cooldown", f"customer was contacted {waited:.0f} hours ago")

    # Offers must still be true when the message goes out.
    if f.offer_expires_at is not None and f.offer_expires_at <= f.now:
        return Verdict("skip", "offer_expired", "the offer or hold expired before sending")
    if f.slot_ref and not f.slot_open:
        return Verdict("skip", "slot_filled", "the opening behind this offer has been filled")

    # Limits that wait, not fail.
    if f.sent_last_24h >= f.daily_send_cap:
        return Verdict("defer", "daily_cap", f"daily cap of {f.daily_send_cap} sends reached")
    if f.in_quiet_hours:
        return Verdict("defer", "quiet_hours", "outside the shop's sending hours")
    return Verdict("ok")

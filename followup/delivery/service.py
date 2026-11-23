"""Send one approved draft, at most once.

The order matters:
1. Claim the draft with one conditional update. Zero rows changed means someone else
   has it, or it was never approved, and we stop.
2. Run every send check again, inside the same transaction as the claim.
3. Record the outbox row, commit, and only then call the provider.
4. Record the result. A timeout leaves the draft in `sending`. We never retry from
   there without asking the provider what happened.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from ..capacity.base import CapacityProvider
from ..guard.limits import in_quiet_hours
from ..guard.send_rules import SendFacts, Verdict, check_send
from ..guard.text_rules import body_hash
from ..platform import eventlog
from ..platform.config import ShopConfig
from ..platform.db import ShopSession
from ..platform.redact import get_logger
from ..platform.timeutil import iso, parse
from ..review.workflow import WorkflowError, get_draft
from ..state.projector import project
from .sender import OutboundMessage, Sender

log = get_logger("followup.delivery")


def _customer_contact(session: ShopSession, customer_id: str, now: datetime
                      ) -> tuple[datetime | None, datetime | None]:
    """Latest contact and latest reply across all of this customer's quotes."""
    quotes = session.rows(
        "SELECT id, customer_id, tech_name, amount_cents, status, created_at, last_contact_at FROM quotes "
        "WHERE shop_id = :shop_id AND customer_id = :cid", cid=customer_id,
    )
    ids = {q["id"] for q in quotes}
    events = [e for e in eventlog.read(session) if e["quote_id"] in ids]
    states = project(quotes, events, now)
    contacts = [s.last_contact for s in states.values() if s.last_contact]
    replies = [s.last_reply for s in states.values() if s.last_reply]
    return (max(contacts) if contacts else None, max(replies) if replies else None)


def _quote_status(session: ShopSession, quote_id: str, now: datetime) -> str:
    q = session.one(
        "SELECT id, customer_id, tech_name, amount_cents, status, created_at, last_contact_at FROM quotes "
        "WHERE shop_id = :shop_id AND id = :id", id=quote_id,
    )
    events = [e for e in eventlog.read(session) if e["quote_id"] == quote_id]
    return project([q], events, now)[quote_id].status


def send(session: ShopSession, draft_id: str, now: datetime, sender: Sender, config: ShopConfig,
         capacity: CapacityProvider, global_sending_enabled: bool = True) -> dict[str, Any]:
    key = f"{session.shop_id}:{draft_id}"
    with session.tx():
        claimed = session.run(
            "UPDATE drafts SET status = 'sending' WHERE shop_id = :shop_id AND id = :id "
            "AND status IN ('approved', 'failed')", id=draft_id,
        )
        d = get_draft(session, draft_id)      # raises not_found for a missing or foreign id
        if not claimed:
            return {"result": "noop", "status": d["status"],
                    "reason": f"draft is {d['status']}, nothing to send"}

        shop = session.one("SELECT sending_enabled FROM shops WHERE shop_id = :shop_id")
        offer = session.one("SELECT * FROM offers WHERE shop_id = :shop_id AND draft_id = :id", id=draft_id)
        last_contact, last_reply = _customer_contact(session, d["customer_id"], now)
        sent_recently = session.one(
            "SELECT COUNT(*) AS n FROM outbox WHERE shop_id = :shop_id AND status = 'sent' AND sent_at > :since",
            since=iso(now - timedelta(hours=24)),
        )["n"]

        slot_ref = offer["slot_ref"] if offer else None
        slot_open, schedule_down = True, False
        if slot_ref:
            try:
                slot_open = capacity.slot_open(session.shop_id, slot_ref, now)
            except Exception:
                schedule_down = True

        verdict = check_send(SendFacts(
            now=now,
            quote_status=_quote_status(session, d["quote_id"], now),
            customer_last_contact=last_contact,
            customer_last_reply=last_reply,
            cooldown_hours=config.cooldown_hours,
            body_hash=body_hash(d["body"]),
            approved_body_hash=d["approved_body_hash"],
            decided_by=d["decided_by"],
            offer_expires_at=parse(offer["expires_at"]) if offer and offer["expires_at"] else None,
            slot_ref=slot_ref,
            slot_open=slot_open,
            shop_sending_enabled=bool(shop and shop["sending_enabled"]),
            global_sending_enabled=global_sending_enabled,
            sent_last_24h=int(sent_recently),
            daily_send_cap=config.daily_send_cap,
            in_quiet_hours=in_quiet_hours(now, config),
        ))
        if verdict.ok and schedule_down:
            verdict = Verdict("defer", "schedule_unavailable", "cannot confirm the opening is still free")

        if not verdict.ok:
            if verdict.action == "skip":
                new_status = "skipped"
                session.run(
                    "UPDATE offers SET status = 'withdrawn' WHERE shop_id = :shop_id AND draft_id = :id "
                    "AND status = 'proposed'", id=draft_id,
                )
            elif verdict.rule in ("hash_mismatch", "no_approval"):
                new_status = "draft"          # approval no longer stands
            else:
                new_status = "approved"       # wait and try again later
            session.run(
                "UPDATE drafts SET status = :st, status_reason = :why WHERE shop_id = :shop_id AND id = :id",
                id=draft_id, st=new_status, why=f"{verdict.rule}: {verdict.reason}",
            )
            log.info("draft %s not sent: %s", draft_id, verdict.rule)
            return {"result": verdict.action, "status": new_status, "rule": verdict.rule, "reason": verdict.reason}

        customer = session.one("SELECT phone_e164 FROM customers WHERE shop_id = :shop_id AND id = :id",
                               id=d["customer_id"])
        # The recipient comes from the database. Nothing a writer produced can change it.
        session.run(
            "INSERT INTO outbox (shop_id, id, draft_id, idempotency_key, to_phone, body, channel, status, attempts, "
            "created_at) VALUES (:shop_id, :id, :draft_id, :key, :to, :body, 'sms', 'pending', 0, :at) "
            "ON CONFLICT DO NOTHING",
            id="X-" + uuid.uuid4().hex[:10], draft_id=draft_id, key=key, to=customer["phone_e164"],
            body=d["body"], at=iso(now),
        )
        session.run(
            "UPDATE outbox SET attempts = attempts + 1, status = 'pending', last_error = NULL "
            "WHERE shop_id = :shop_id AND draft_id = :id", id=draft_id,
        )
        message = OutboundMessage(key, customer["phone_e164"], d["body"], "sms")
        log.info("sending draft %s to %s", draft_id, customer["phone_e164"])   # the logger masks the number
        quote_id = d["quote_id"]

    # Committed. A crash from here leaves the draft in `sending`, which nothing will resend.
    result = sender.send(message)

    with session.tx():
        if result.status == "accepted":
            _mark_sent(session, draft_id, quote_id, now, result.provider_message_id)
            return {"result": "sent", "status": "sent", "provider_message_id": result.provider_message_id}
        if result.status == "rejected":
            session.run(
                "UPDATE drafts SET status = 'failed', status_reason = :why WHERE shop_id = :shop_id AND id = :id",
                id=draft_id, why=result.error or "provider rejected",
            )
            session.run(
                "UPDATE outbox SET status = 'failed', last_error = :why WHERE shop_id = :shop_id AND draft_id = :id",
                id=draft_id, why=result.error or "provider rejected",
            )
            return {"result": "failed", "status": "failed", "reason": result.error}
        session.run(
            "UPDATE outbox SET status = 'unknown', last_error = :why WHERE shop_id = :shop_id AND draft_id = :id",
            id=draft_id, why=result.error or "timeout",
        )
        session.run(
            "UPDATE drafts SET status_reason = 'provider did not answer, reconcile before any retry' "
            "WHERE shop_id = :shop_id AND id = :id", id=draft_id,
        )
        return {"result": "unknown", "status": "sending", "reason": "provider did not answer"}


def _mark_sent(session: ShopSession, draft_id: str, quote_id: str, now: datetime, provider_id: str | None) -> None:
    session.run(
        "UPDATE drafts SET status = 'sent', status_reason = NULL WHERE shop_id = :shop_id AND id = :id", id=draft_id,
    )
    session.run(
        "UPDATE outbox SET status = 'sent', sent_at = :at, provider_message_id = :pid "
        "WHERE shop_id = :shop_id AND draft_id = :id", id=draft_id, at=iso(now), pid=provider_id,
    )
    session.run(
        "UPDATE offers SET status = 'sent' WHERE shop_id = :shop_id AND draft_id = :id AND status = 'proposed'",
        id=draft_id,
    )
    # Close the loop. Our own send becomes an event, so last contact has one source.
    eventlog.append(session, {
        "event_id": "sent:" + draft_id, "quote_id": quote_id, "type": "message_sent",
        "occurred_at": iso(now), "channel": "sms", "direction": "outbound",
        "raw": {"source": "followup-engine", "draft_id": draft_id},
    })


def reconcile(session: ShopSession, draft_id: str, now: datetime, sender: Sender) -> dict[str, Any]:
    """Settle a draft stuck in `sending` by asking the provider what happened."""
    with session.tx():
        d = get_draft(session, draft_id)
        if d["status"] != "sending":
            return {"result": "noop", "status": d["status"], "reason": "draft is not waiting on the provider"}
        found = sender.lookup(f"{session.shop_id}:{draft_id}")
        if found is not None and found.status == "accepted":
            _mark_sent(session, draft_id, d["quote_id"], now, found.provider_message_id)
            return {"result": "sent", "status": "sent", "reason": "provider confirms delivery"}
        session.run(
            "UPDATE drafts SET status = 'failed', status_reason = 'provider has no record, safe to retry' "
            "WHERE shop_id = :shop_id AND id = :id", id=draft_id,
        )
        session.run(
            "UPDATE outbox SET status = 'failed', last_error = 'provider has no record' "
            "WHERE shop_id = :shop_id AND draft_id = :id", id=draft_id,
        )
        return {"result": "failed", "status": "failed", "reason": "provider has no record, safe to retry"}

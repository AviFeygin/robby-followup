"""Draft lifecycle up to approval.

draft -> approved | rejected | expired. An edit clears approval. Approval stores a
hash of the exact text, and only a human can approve.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from typing import Any

from .. import POLICY_VERSION
from ..guard.send_rules import is_human
from ..guard.text_rules import TextContext, body_hash, check_text
from ..platform.db import ShopSession
from ..platform.timeutil import iso, parse
from ..policy.engine import CustomerFacts, QuoteFacts

LIVE = ("draft", "approved", "sending", "failed")
ROLES = ("owner", "tech")


class WorkflowError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _not_found() -> WorkflowError:
    # Another shop's id reads exactly like a missing id.
    return WorkflowError("not_found", "draft not found")


def get_draft(session: ShopSession, draft_id: str) -> dict[str, Any]:
    row = session.one("SELECT * FROM drafts WHERE shop_id = :shop_id AND id = :id", id=draft_id)
    if row is None:
        raise _not_found()
    return row


def list_drafts(session: ShopSession) -> list[dict[str, Any]]:
    drafts = session.rows("SELECT * FROM drafts WHERE shop_id = :shop_id ORDER BY created_at, id")
    offers = {o["draft_id"]: o for o in session.rows("SELECT * FROM offers WHERE shop_id = :shop_id")}
    outbox = {o["draft_id"]: o for o in session.rows("SELECT * FROM outbox WHERE shop_id = :shop_id")}
    for d in drafts:
        d["offer"] = offers.get(d["id"])
        d["outbox"] = outbox.get(d["id"])
    return drafts


def gather_facts(session: ShopSession, now: datetime, cooldown_hours: float
                 ) -> tuple[dict[str, QuoteFacts], dict[str, CustomerFacts], int]:
    """What the policy needs from the workflow tables."""
    snoozed: dict[str, datetime] = {}
    framings: dict[str, set[str]] = {}
    customers: dict[str, CustomerFacts] = {}
    for d in session.rows(
        "SELECT quote_id, customer_id, status, framing, decided_at FROM drafts WHERE shop_id = :shop_id"
    ):
        if d["status"] in LIVE:
            customers[d["customer_id"]] = CustomerFacts(has_live_draft=True)
        if d["status"] == "rejected" and d["decided_at"]:
            until = parse(d["decided_at"]) + timedelta(hours=cooldown_hours)
            if until > snoozed.get(d["quote_id"], until - timedelta(days=1)):
                snoozed[d["quote_id"]] = until
        if d["status"] == "sent" and d["framing"]:
            framings.setdefault(d["quote_id"], set()).add(d["framing"])

    costed: set[str] = set()
    spent = 0
    month = now.strftime("%Y-%m")
    for o in session.rows(
        "SELECT quote_id, value_cents, status, created_at FROM offers WHERE shop_id = :shop_id AND value_cents > 0"
    ):
        if o["status"] == "sent":
            costed.add(o["quote_id"])
        if o["status"] in ("proposed", "sent") and o["created_at"][:7] == month:
            spent += int(o["value_cents"])

    quote_ids = set(snoozed) | set(framings) | costed
    quotes = {
        qid: QuoteFacts(snoozed_until=snoozed.get(qid), had_costed_offer=qid in costed,
                        prior_framings=frozenset(framings.get(qid, ())))
        for qid in quote_ids
    }
    return quotes, customers, spent


def create_draft(session: ShopSession, cand: dict[str, Any], body: str, written_by: str,
                 ctx: TextContext, now: datetime) -> str | None:
    """Store a draft. Returns None if the customer already has a live one."""
    draft_id = "D-" + uuid.uuid4().hex[:10]
    framing = cand.get("framing") or {}
    with session.tx():
        inserted = session.run(
            "INSERT INTO drafts (shop_id, id, quote_id, customer_id, status, tier, reason_code, reason, strategy, "
            "framing, body, written_by, requires_owner, approver_note, created_at, allowed_json, signals_json, "
            "policy_version) VALUES (:shop_id, :id, :quote_id, :customer_id, 'draft', :tier, :reason_code, :reason, "
            ":strategy, :framing, :body, :written_by, :requires_owner, :note, :created_at, :allowed, :signals, :pv) "
            "ON CONFLICT DO NOTHING",
            id=draft_id, quote_id=cand["quote_id"], customer_id=cand["customer_id"], tier=cand["tier"],
            reason_code=cand["reason_code"], reason=cand["reason"], strategy=cand["strategy"],
            framing=framing.get("kind"), body=body, written_by=written_by,
            requires_owner=1 if cand.get("requires_owner") else 0, note=cand.get("approver_note") or "",
            created_at=iso(now), allowed=json.dumps(ctx.to_dict()), signals=json.dumps(cand.get("signals", {})),
            pv=POLICY_VERSION,
        )
        if not inserted:
            return None
        offer = cand.get("offer")
        if offer:
            session.run(
                "INSERT INTO offers (shop_id, id, draft_id, quote_id, kind, value_cents, expires_at, slot_ref, "
                "status, created_at) VALUES (:shop_id, :id, :draft_id, :quote_id, :kind, :value, :expires, :slot, "
                "'proposed', :created_at)",
                id="O-" + uuid.uuid4().hex[:10], draft_id=draft_id, quote_id=cand["quote_id"], kind=offer["kind"],
                value=int(offer["value_cents"]), expires=offer.get("expires_at"), slot=offer.get("slot_ref"),
                created_at=iso(now),
            )
    return draft_id


def approve(session: ShopSession, draft_id: str, actor: str, role: str, now: datetime) -> dict[str, Any]:
    if not is_human(actor):
        raise WorkflowError("not_human", "only a person can approve a draft")
    if role not in ROLES:
        raise WorkflowError("bad_role", "role must be owner or tech")
    with session.tx():
        d = get_draft(session, draft_id)
        if d["status"] != "draft":
            raise WorkflowError("bad_state", f"draft is {d['status']}, not awaiting approval")
        if d["requires_owner"] and role != "owner":
            raise WorkflowError("owner_required", "a draft with a costed offer needs the owner's approval")
        session.run(
            "UPDATE drafts SET status = 'approved', approved_body_hash = :h, decided_by = :actor, decided_at = :at, "
            "status_reason = NULL WHERE shop_id = :shop_id AND id = :id AND status = 'draft'",
            id=draft_id, h=body_hash(d["body"]), actor=actor, at=iso(now),
        )
        return get_draft(session, draft_id)


def edit(session: ShopSession, draft_id: str, body: str, actor: str, now: datetime) -> dict[str, Any]:
    """Change the text. Approval is cleared. Guard findings come back as warnings for the person."""
    with session.tx():
        d = get_draft(session, draft_id)
        if d["status"] not in ("draft", "approved"):
            raise WorkflowError("bad_state", f"draft is {d['status']} and can no longer be edited")
        ctx = TextContext.from_dict(json.loads(d["allowed_json"]))
        warnings = [v.to_dict() for v in check_text(body, ctx)]
        if any(w["rule"] == "shape" for w in warnings):
            raise WorkflowError("bad_text", "; ".join(w["detail"] for w in warnings if w["rule"] == "shape"))
        session.run(
            "UPDATE drafts SET body = :body, status = 'draft', approved_body_hash = NULL, decided_by = NULL, "
            "decided_at = NULL, written_by = :by WHERE shop_id = :shop_id AND id = :id",
            id=draft_id, body=body,
            by=d["written_by"] if d["written_by"].endswith("+edited") else d["written_by"] + "+edited",
        )
        out = get_draft(session, draft_id)
        out["warnings"] = warnings
        return out


def _withdraw_offer(session: ShopSession, draft_id: str) -> None:
    session.run(
        "UPDATE offers SET status = 'withdrawn' WHERE shop_id = :shop_id AND draft_id = :id AND status = 'proposed'",
        id=draft_id,
    )


def reject(session: ShopSession, draft_id: str, actor: str, now: datetime) -> dict[str, Any]:
    if not is_human(actor):
        raise WorkflowError("not_human", "only a person can reject a draft")
    with session.tx():
        d = get_draft(session, draft_id)
        if d["status"] not in ("draft", "approved"):
            raise WorkflowError("bad_state", f"draft is {d['status']}")
        session.run(
            "UPDATE drafts SET status = 'rejected', decided_by = :actor, decided_at = :at, approved_body_hash = NULL "
            "WHERE shop_id = :shop_id AND id = :id",
            id=draft_id, actor=actor, at=iso(now),
        )
        _withdraw_offer(session, draft_id)
        return get_draft(session, draft_id)


def expire_for_closed_quotes(session: ShopSession, closed_quote_ids: set[str]) -> list[str]:
    """A draft about a quote that has since closed is expired, not left to be sent."""
    expired = []
    with session.tx():
        for d in session.rows(
            "SELECT id, quote_id FROM drafts WHERE shop_id = :shop_id AND status IN ('draft', 'approved')"
        ):
            if d["quote_id"] in closed_quote_ids:
                session.run(
                    "UPDATE drafts SET status = 'expired', status_reason = 'quote closed' "
                    "WHERE shop_id = :shop_id AND id = :id",
                    id=d["id"],
                )
                _withdraw_offer(session, d["id"])
                expired.append(d["id"])
    return expired

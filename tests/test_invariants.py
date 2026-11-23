"""F. Invariants under random sequences of actions.

Each run takes a random walk through everything a person, the engine, a customer and
a flaky provider can do, and checks the guardrails after every step. This is the test
that finds the cases nobody thought to write.
"""
from __future__ import annotations

import json
import random

import pytest

from conftest import mini
from followup.bootstrap import read_seed
from followup.delivery import ScriptedSender
from followup.guard import TextContext, body_hash, check_text
from followup.platform.config import demo_config
from followup.platform.timeutil import hours_between, iso, parse
from datetime import timedelta

SHOP = "s1"
COOLDOWN = 72.0


class CountingSender(ScriptedSender):
    """Counts real deliveries per key, the thing a customer would actually receive."""

    def __init__(self):
        super().__init__()
        self.deliveries: dict[str, int] = {}
        self.delivered_at: dict[str, str] = {}
        self.clock = None

    def send(self, message):
        before = len(self.delivered)
        result = super().send(message)
        if len(self.delivered) > before:
            self.deliveries[message.idempotency_key] = self.deliveries.get(message.idempotency_key, 0) + 1
            self.delivered_at[message.idempotency_key] = self.clock
        return result


def _check(engine, sender, now):
    session = engine.session(SHOP)
    drafts = {d["id"]: d for d in session.rows("SELECT * FROM drafts WHERE shop_id = :shop_id")}
    outbox = session.rows("SELECT * FROM outbox WHERE shop_id = :shop_id")
    quotes = {q["id"]: q for q in session.rows("SELECT * FROM quotes WHERE shop_id = :shop_id")}
    events = session.rows("SELECT * FROM events WHERE shop_id = :shop_id")
    accepted = {e["quote_id"]: e["occurred_at"] for e in events if e["type"] == "quote_accepted"}
    replies = [(e["quote_id"], e["occurred_at"]) for e in events if e["type"] == "customer_replied"]

    # 1. No draft is delivered more than once.
    assert all(n == 1 for n in sender.deliveries.values()), sender.deliveries
    assert len({o["draft_id"] for o in outbox}) == len(outbox)

    # 2. One live draft per customer.
    live = [d["customer_id"] for d in drafts.values() if d["status"] in ("draft", "approved", "sending", "failed")]
    assert len(live) == len(set(live))

    # Every real delivery, with the time it left.
    sends = []
    for key, at in sender.delivered_at.items():
        d = drafts[key.split(":", 1)[1]]
        sends.append((d["customer_id"], parse(at), d))

    for customer, at, d in sends:
        # 3. Never about a quote that was closed when the message left.
        assert quotes[d["quote_id"]]["status"] == "open"
        if d["quote_id"] in accepted:
            assert parse(accepted[d["quote_id"]]) >= at, "sent after the quote was accepted"  # equal: the send went first
        # 4. Exactly the text a person approved.
        assert d["approved_body_hash"] == body_hash(d["body"]) and d["decided_by"].startswith("user:")
        # 5. No number outside the draft's allowed set.
        ctx = TextContext.from_dict(json.loads(d["allowed_json"]))
        assert not [v for v in check_text(d["body"], ctx) if v.rule in ("money", "dates", "contact_details")]

    # 6. No two messages to one customer inside the cooldown, unless they replied in between.
    customer_quotes: dict[str, set] = {}
    for q in quotes.values():
        customer_quotes.setdefault(q["customer_id"], set()).add(q["id"])
    by_customer: dict[str, list] = {}
    for customer, at, _ in sends:
        by_customer.setdefault(customer, []).append(at)
    for customer, times in by_customer.items():
        times.sort()
        for earlier, later in zip(times, times[1:]):
            if hours_between(earlier, later) < COOLDOWN:
                # A reply can land in the same instant as the send that answers it, hence <=.
                replied = any(q in customer_quotes[customer] and earlier < parse(t) <= later for q, t in replies)
                assert replied, f"{customer} messaged twice in {hours_between(earlier, later):.0f} hours"


@pytest.mark.parametrize("seed", range(12))
def test_guardrails_hold_through_random_sequences(seed):
    rng = random.Random(seed)
    quotes, events = read_seed()
    sender = CountingSender()
    # Quiet hours are off here so the walk spends its steps sending, not waiting for morning.
    engine = mini(quotes, events, demo_config().with_(quiet_hours_enabled=False), sender=sender)
    now = parse("2026-08-17T14:00:00Z")
    open_ids = [q["id"] for q in quotes if q["status"] == "open"]
    counter = 0

    def drafts(*statuses):
        rows = engine.session(SHOP).rows("SELECT id, status, quote_id FROM drafts WHERE shop_id = :shop_id")
        return [r for r in rows if not statuses or r["status"] in statuses]

    for _ in range(70):
        counter += 1
        t = iso(now)
        sender.clock = t
        action = rng.choice(["run", "run", "approve", "approve", "send", "send", "send", "retry", "edit", "reject",
                             "accept", "view", "reply", "tech_text", "advance", "advance", "flaky", "reconcile",
                             "capacity"])
        try:
            if action == "run":
                engine.run(SHOP, t)
            elif action == "approve":
                for d in rng.sample(drafts("draft"), k=min(4, len(drafts("draft")))):
                    engine.approve(SHOP, d["id"], "user:owner", "owner", t)
            elif action in ("send", "retry"):
                pool = drafts("approved", "failed", "sending", "sent", "draft")
                for d in rng.sample(pool, k=min(4, len(pool))):
                    engine.send(SHOP, d["id"], t)
            elif action == "edit":
                pool = drafts("draft", "approved")
                if pool:
                    engine.edit(SHOP, rng.choice(pool)["id"], "Hi there, quick note about your quote.", "user:owner", t)
            elif action == "reject":
                pool = drafts("draft", "approved")
                if pool:
                    engine.reject(SHOP, rng.choice(pool)["id"], "user:owner", t)
            elif action == "accept":
                engine.accept_quote(SHOP, rng.choice(open_ids), t)
            elif action in ("view", "reply", "tech_text"):
                kind = {"view": "quote_viewed", "reply": "customer_replied", "tech_text": "message_sent"}[action]
                extra = {"direction": "outbound"} if action == "tech_text" else {}
                engine.ingest(SHOP, [{"event_id": f"walk-{seed}-{counter}", "type": kind,
                                      "quote_id": rng.choice(open_ids), "timestamp": t, **extra}])
            elif action == "advance":
                now += timedelta(hours=rng.choice([0.5, 3, 12, 30, 80]))
            elif action == "flaky":
                sender.script = [rng.choice(["reject", "timeout", "timeout_but_delivered", "accept"]) for _ in range(3)]
            elif action == "reconcile":
                for d in drafts("sending"):
                    engine.reconcile(SHOP, d["id"], t)
            elif action == "capacity":
                engine.set_capacity(SHOP, booked_hours=rng.choice([0, 80, 160, 200]), median_booked_hours=160,
                                    available_hours=200)
        except Exception as exc:                      # workflow refusals are expected, crashes are not
            assert type(exc).__name__ == "WorkflowError", repr(exc)
        _check(engine, sender, now)

    assert sender.deliveries, "the walk never sent anything, so it proved nothing"

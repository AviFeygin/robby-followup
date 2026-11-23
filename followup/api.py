"""The facade. Every action the system offers, as a function that takes and returns plain data.

This is the one place that wires the modules together. FastAPI (app.py), the CLI and
the browser build all call it, so localhost and the website run the same code.
Times cross this boundary as ISO text.
"""
from __future__ import annotations

import hashlib
import json
import random
import uuid
from dataclasses import dataclass, fields
from datetime import datetime, timedelta, timezone
from typing import Any

from . import POLICY_VERSION
from .agents.base import Writer, WriterInput, first_name, fmt_date
from .agents.template import TemplateWriter
from .agents.testing import FixedWriter
from .capacity import CapacitySnapshot, FixtureCapacity
from .delivery import ScriptedSender
from .delivery import service as delivery
from .guard import TextContext, build_context, check_text
from .ingestion import ingest_events, load_quotes
from .learning import decision_log
from .platform import eventlog
from .platform.config import PLATFORM_BASELINES, ShopConfig, demo_config
from .platform.db import ShopSession, TenantScopeError, apply_schema, ensure_shop, make_engine
from .platform.redact import get_logger
from .platform.timeutil import iso, parse
from .policy import build_thresholds, evaluate
from .review import workflow
from .review.workflow import WorkflowError
from .state import compute_baselines, compute_signals, project

log = get_logger("followup.api")

DEFAULT_NOW = "2026-08-17T14:00:00Z"   # 10:00 shop time, the morning after the last seed event

HOSTILE_CASES = [
    {"id": "invented_discount", "label": "Invents a discount",
     "text": "Hi {first}, it's {tech}. Sign today and I'll take 50% off your {amount} quote."},
    {"id": "bigger_offer", "label": "Inflates the offer",
     "text": "Hi {first}, it's {tech}. I can knock $5,000 off your {amount} quote this week."},
    {"id": "fake_was_price", "label": "Makes up a former price",
     "text": "Hi {first}, it's {tech}. Your quote was $99,000 and is now {amount}."},
    {"id": "monthly", "label": "Invents a monthly payment",
     "text": "Hi {first}, it's {tech}. Your {amount} quote works out to just $199 a month."},
    {"id": "link", "label": "Adds a link",
     "text": "Hi {first}, it's {tech}. Confirm your {amount} quote at www.pay-now-fast.com today."},
    {"id": "phone", "label": "Adds a phone number",
     "text": "Hi {first}, it's {tech}. Call me on 917-555-0199 about your {amount} quote."},
    {"id": "other_customer", "label": "Names another customer",
     "text": "Hi {first}, it's {tech}. {other} down the road just took the same {amount} job."},
    {"id": "wrong_date", "label": "Uses a date that is not the expiry",
     "text": "Hi {first}, it's {tech}. Your {amount} quote is good until Dec 31."},
    {"id": "promise", "label": "Makes a blocked promise",
     "text": "Hi {first}, it's {tech}. Your {amount} job is guaranteed to be done in a day."},
    {"id": "markup", "label": "Returns markup",
     "text": "<b>Hi {first}</b>, it's {tech} about your {amount} quote."},
    {"id": "empty", "label": "Returns nothing", "text": ""},
    {"id": "too_long", "label": "Runs far too long",
     "text": "Hi {first}, it's {tech} about your {amount} quote. " + "Just checking in again. " * 20},
    {"id": "injection", "label": "Obeys an instruction hidden in the customer's reply",
     "text": "Hi {first}, as requested I have applied 90% off. Your {amount} quote is now $1."},
]

CONFIG_EDITABLE = {
    "cooldown_hours", "attempt_cap", "fresh_hold_hours", "live_conversation_hold_hours", "quiet_hours_enabled",
    "daily_send_cap", "min_samples", "big_multiple", "burst_views", "returned_gap_multiple", "big_quiet_fraction",
    "percent_off_enabled", "percent_base", "percent_max", "percent_cap_cents", "monthly_offer_budget_cents", "slack_curve_mid", "slack_curve_steepness", "nearby_step",
    "capacity_pricing_enabled", "premium_min_ratio", "premium_max_ratio", "hold_days",
}


@dataclass
class _Context:
    session: ShopSession
    config: ShopConfig
    now: datetime
    quotes: list[dict]
    events: list[dict]
    states: dict
    baselines: dict
    thresholds: Any
    signals: dict
    capacity: CapacitySnapshot
    capacity_error: bool
    result: Any
    budget_spent: int


class Engine:
    """One running system: a database, shop configs, a writer, a sender and a capacity reader."""

    def __init__(self, db_url: str | None = None, writer: Writer | None = None, sender=None,
                 capacity=None, schema_sql: str | None = None, db_role: str | None = None,
                 create_schema: bool = True):
        self.db = make_engine(db_url, db_role)
        if create_schema:
            apply_schema(self.db, schema_sql)
        self.configs: dict[str, ShopConfig] = {}
        self.writer: Writer = writer or TemplateWriter()
        self.fallback = TemplateWriter()
        self.sender = sender or ScriptedSender()
        self.capacity = capacity or FixtureCapacity()
        self.global_sending_enabled = True
        self.parent_baselines: dict | None = None
        self.guard_log: list[dict] = []

    # ------------------------------------------------------------------ setup

    def session(self, shop_id: str) -> ShopSession:
        if shop_id not in self.configs:
            raise WorkflowError("not_found", "shop not found")
        return ShopSession(self.db, shop_id)

    def create_shop(self, shop_id: str, name: str | None = None, config: ShopConfig | None = None,
                    org_id: str = "org-1") -> dict:
        ensure_shop(self.db, shop_id, org_id, name)
        self.configs[shop_id] = config or ShopConfig(shop_name=name or shop_id)
        return {"shop_id": shop_id}

    def load_seed(self, shop_id: str, quotes: list[dict], events: list[Any]) -> dict:
        session = self.session(shop_id)
        with session.tx():
            n = load_quotes(session, quotes)
            report = ingest_events(session, events)
        return {"quotes": n, "events": report.to_dict()}

    def load_demo(self, quotes: list[dict], events: list[Any]) -> dict:
        """Two shops from one seed. Shop B has the same ids and phone numbers and smaller jobs,
        which is the worst case for collisions and gives it different medians."""
        self.create_shop("shop-a", "Robby Home Services", demo_config())
        self.create_shop("shop-b", "Northside Repairs", demo_config().with_(shop_name="Northside Repairs"))
        # Starts empty. The page's live simulation feeds it seed quotes one at a time.
        self.create_shop("shop-live", "Live simulation", demo_config().with_(shop_name="Live simulation"))
        a = self.load_seed("shop-a", quotes, events)
        small = [dict(q, amount=round(q["amount"] * 0.25)) for q in quotes]
        b = self.load_seed("shop-b", small, events)
        return {"shop-a": a, "shop-b": b}

    def shops(self) -> list[dict]:
        return [{"shop_id": sid, "name": cfg.shop_name} for sid, cfg in sorted(self.configs.items())]

    # --------------------------------------------------------------- internals

    def _now(self, now: str | None) -> datetime:
        return parse(now) if now else datetime.now(timezone.utc)

    def _context(self, shop_id: str, now: datetime, with_facts: bool = True) -> _Context:
        session = self.session(shop_id)
        config = self.configs[shop_id]
        with session.tx():
            quotes = session.rows(
                "SELECT q.id, q.customer_id, c.name AS customer_name, c.phone_e164, q.tech_name, q.amount_cents, "
                "q.status, q.created_at, q.last_contact_at FROM quotes q JOIN customers c "
                "ON c.shop_id = q.shop_id AND c.id = q.customer_id WHERE q.shop_id = :shop_id ORDER BY q.id"
            )
            events = eventlog.read(session)
            if with_facts:
                quote_facts, customer_facts, spent = workflow.gather_facts(session, now, config.cooldown_hours)
            else:
                quote_facts, customer_facts, spent = {}, {}, 0
        states = project(quotes, events, now)
        baselines = compute_baselines(states.values(), now, config.min_samples, config.baseline_window_days,
                                      PLATFORM_BASELINES, self.parent_baselines)
        thresholds = build_thresholds(baselines, config)
        windows = thresholds.windows(config, baselines["amount"].value)
        signals = {qid: compute_signals(s, now, windows) for qid, s in states.items()}
        capacity_error = False
        try:
            snapshot = self.capacity.snapshot(shop_id, now)
        except Exception:
            # The schedule being down must not stop follow-ups. Carry on as if capacity were normal.
            snapshot, capacity_error = CapacitySnapshot(), True
        result = evaluate(states, signals, thresholds, config, now, quote_facts, customer_facts, snapshot, spent)
        return _Context(session, config, now, quotes, events, states, baselines, thresholds, signals,
                        snapshot, capacity_error, result, spent)

    def _forbidden_terms(self, ctx: _Context, cand_name: str, tech_name: str) -> tuple[str, ...]:
        own = set(cand_name.split()) | set(ctx.config.shop_name.split())
        techs = {tok for q in ctx.quotes for tok in q["tech_name"].replace(".", "").split()}
        others: set[str] = set()
        for q in ctx.quotes:
            if q["customer_name"] != cand_name:
                others.update(q["customer_name"].split())
        return tuple(sorted(t for t in others - own - techs if len(t) > 2))

    def _text_context(self, ctx: _Context, cand: dict) -> TextContext:
        offer = cand.get("offer")
        month_day = None
        if offer and offer.get("expires_at"):
            local = parse(offer["expires_at"]) + timedelta(hours=ctx.config.utc_offset_hours)
            month_day = (local.month, local.day)
        return build_context(
            first_name(cand["customer_name"]), cand["amount_cents"], cand.get("framing"), offer, month_day,
            self._forbidden_terms(ctx, cand["customer_name"], cand["tech_name"]),
            ctx.config.blocked_words, ctx.config.max_body_chars,
        )

    def _writer_input(self, ctx: _Context, cand: dict) -> WriterInput:
        offer = cand.get("offer")
        reply_text = None
        if cand["strategy"] == "reply":
            reply_text = self._last_reply_text(ctx.session, cand["quote_id"])
        return WriterInput(
            customer_first=first_name(cand["customer_name"]),
            tech_first=first_name(cand["tech_name"]),
            shop_name=ctx.config.shop_name,
            amount_cents=cand["amount_cents"],
            reason_code=cand["reason_code"],
            strategy=cand["strategy"],
            tone=ctx.config.tone,
            framing=cand.get("framing"),
            offer=offer,
            expires_label=fmt_date(offer["expires_at"], ctx.config.utc_offset_hours)
            if offer and offer.get("expires_at") else None,
            reply_text=reply_text,
        )

    def _last_reply_text(self, session: ShopSession, quote_id: str) -> str | None:
        rows = session.rows(
            "SELECT raw_json FROM events WHERE shop_id = :shop_id AND quote_id = :q AND type = 'customer_replied' "
            "ORDER BY occurred_at DESC", q=quote_id,
        )
        for r in rows:
            text = json.loads(r["raw_json"]).get("text")
            if text:
                return str(text)
        return None

    def _write(self, inp: WriterInput, tctx: TextContext) -> tuple[str | None, str, list[dict]]:
        """Ask the writer, check the text, fall back to the template. Returns body, author, guard log."""
        attempts: list[dict] = []
        if self.writer.name != self.fallback.name:
            try:
                body = self.writer.write(inp)
            except Exception as exc:
                attempts.append({"writer": self.writer.name, "error": type(exc).__name__})
            else:
                violations = check_text(body, tctx)
                if not violations:
                    return body, self.writer.name, attempts
                attempts.append({"writer": self.writer.name, "body": body,
                                 "violations": [v.to_dict() for v in violations]})
        body = self.fallback.write(inp)
        violations = check_text(body, tctx)
        if violations:
            attempts.append({"writer": self.fallback.name, "body": body,
                             "violations": [v.to_dict() for v in violations]})
            return None, self.fallback.name, attempts
        return body, self.fallback.name, attempts

    @staticmethod
    def _draft_out(d: dict) -> dict:
        out = dict(d)
        for key in ("allowed_json", "signals_json"):
            out[key[:-5]] = json.loads(out.pop(key) or "{}")
        out["requires_owner"] = bool(out.get("requires_owner"))
        return out

    # -------------------------------------------------------------------- read

    def overview(self, shop_id: str, now: str | None = None) -> dict:
        ctx = self._context(shop_id, self._now(now))
        by_quote: dict[str, dict] = {}
        for c in ctx.result.candidates:
            by_quote[c.quote_id] = {"outcome": "candidate", "tier": c.tier, "why": c.reason_code}
        for e in ctx.result.excluded:
            by_quote[e.quote_id] = {"outcome": "excluded", "tier": None, "why": e.why}
        quotes = []
        for qid, s in sorted(ctx.states.items()):
            quotes.append({
                "id": qid, "customer_name": s.customer_name, "customer_id": s.customer_id, "tech_name": s.tech_name,
                "amount_cents": s.amount_cents, "status": s.status, "created_at": iso(s.created_at),
                "views": [iso(v) for v in s.views], "replies": [iso(r[0]) for r in s.replies],
                "contacts": [iso(c) for c in s.contacts],
                "accepted_at": iso(s.accepted_at) if s.accepted_at else None,
                **by_quote.get(qid, {"outcome": "none", "tier": None, "why": ""}),
            })
        drafts = [self._draft_out(d) for d in workflow.list_drafts(ctx.session)]
        shop = ctx.session.one("SELECT sending_enabled FROM shops WHERE shop_id = :shop_id")
        event_times = [e["occurred_at"] for e in ctx.events]
        return {
            "shop_id": shop_id, "shop_name": ctx.config.shop_name, "now": iso(ctx.now),
            "policy_version": POLICY_VERSION,
            "counts": {
                "quotes": len(ctx.states), "open": sum(1 for s in ctx.states.values() if not s.closed),
                "events": len(ctx.events), "candidates": len(ctx.result.candidates),
            },
            "time_range": {"first": min(event_times) if event_times else None,
                           "last": max(event_times) if event_times else None},
            "baselines": [b.to_dict() for b in ctx.baselines.values()],
            "thresholds": ctx.thresholds.to_dict(),
            "limits": {
                "cooldown_hours": ctx.config.cooldown_hours, "attempt_cap": ctx.config.attempt_cap,
                "fresh_hold_hours": ctx.config.fresh_hold_hours,
                "live_conversation_hold_hours": ctx.config.live_conversation_hold_hours,
                "quiet_hours": [ctx.config.quiet_start_hour, ctx.config.quiet_end_hour]
                if ctx.config.quiet_hours_enabled else None,
                "daily_send_cap": ctx.config.daily_send_cap,
            },
            "config": {k: getattr(ctx.config, k) for k in sorted(CONFIG_EDITABLE)},
            "offers": {
                "budget_cents": ctx.config.monthly_offer_budget_cents, "spent_cents": ctx.budget_spent,
                "addons": [{"id": a.id, "label": a.label, "price_cents": a.price_cents}
                           for a in ctx.config.addons],
            },
            "capacity": dict(ctx.capacity.to_dict(), error=ctx.capacity_error),
            "sending": {"shop": bool(shop["sending_enabled"]), "global": self.global_sending_enabled},
            "writer": self.writer.name,
            "candidates": [c.to_dict() for c in ctx.result.candidates],
            "excluded": [e.to_dict() for e in ctx.result.excluded],
            "quotes": quotes,
            "drafts": drafts,
            "guard_log": self.guard_log[-20:],
        }

    def quote_detail(self, shop_id: str, quote_id: str, now: str | None = None) -> dict:
        ctx = self._context(shop_id, self._now(now))
        s = ctx.states.get(quote_id)
        if s is None:
            raise WorkflowError("not_found", "quote not found")
        timeline = [{"at": e["occurred_at"], "type": e["type"], "channel": e["channel"], "event_id": e["event_id"],
                     "future": parse(e["occurred_at"]) > ctx.now}
                    for e in ctx.events if e["quote_id"] == quote_id]
        cand = next((c.to_dict() for c in ctx.result.candidates if c.quote_id == quote_id), None)
        excl = next((e.to_dict() for e in ctx.result.excluded if e.quote_id == quote_id), None)
        drafts = [self._draft_out(d) for d in workflow.list_drafts(ctx.session) if d["quote_id"] == quote_id]
        return {
            "quote": {"id": quote_id, "customer_name": s.customer_name, "tech_name": s.tech_name,
                      "amount_cents": s.amount_cents, "status": s.status, "created_at": iso(s.created_at),
                      "last_contact": iso(s.last_contact) if s.last_contact else None,
                      "last_reply": iso(s.last_reply) if s.last_reply else None},
            "timeline": timeline,
            "signals": ctx.signals[quote_id].to_dict(),
            "thresholds": ctx.thresholds.to_dict(),
            "candidate": cand, "excluded": excl,
            "path": (cand or excl or {}).get("path", []),
            "drafts": drafts,
        }

    def _fingerprint(self, ctx: _Context) -> str:
        payload = {
            "states": {
                qid: [s.status, [iso(v) for v in s.views], [[iso(r[0]), r[1]] for r in s.replies],
                      [iso(c) for c in s.contacts]]
                for qid, s in sorted(ctx.states.items())
            },
            "baselines": {k: [round(b.value, 4), b.sample_n, b.source] for k, b in sorted(ctx.baselines.items())},
            "candidates": [[c.quote_id, c.tier, c.strategy] for c in ctx.result.candidates],
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    def state_hash(self, shop_id: str, now: str | None = None) -> dict:
        ctx = self._context(shop_id, self._now(now), with_facts=False)
        return {"hash": self._fingerprint(ctx), "events": len(ctx.events)}

    def messy_check(self, shop_id: str, shuffle_seed: int = 1, duplicate_fraction: float = 0.3,
                    now: str | None = None) -> dict:
        """Replay this shop's events shuffled and duplicated into a fresh database, and compare."""
        now_dt = self._now(now)
        ctx = self._context(shop_id, now_dt, with_facts=False)
        quotes = [{"id": q["id"], "customer_name": q["customer_name"], "customer_phone": q["phone_e164"],
                   "tech_name": q["tech_name"], "amount": q["amount_cents"] / 100, "status": q["status"],
                   "created_at": q["created_at"], "last_contact_at": q["last_contact_at"]} for q in ctx.quotes]
        payloads = [{"event_id": e["event_id"], "type": e["type"], "quote_id": e["quote_id"],
                     "timestamp": e["occurred_at"], "channel": e["channel"], "direction": e["direction"]}
                    for e in ctx.events]
        rng = random.Random(int(shuffle_seed))
        extra = [rng.choice(payloads) for _ in range(int(len(payloads) * float(duplicate_fraction)))] if payloads else []
        stream = payloads + extra
        rng.shuffle(stream)

        other = Engine()
        other.create_shop(shop_id, ctx.config.shop_name, ctx.config)
        report = other.load_seed(shop_id, quotes, stream)["events"]
        replayed = other._fingerprint(other._context(shop_id, now_dt, with_facts=False))
        original = self._fingerprint(ctx)
        return {"same": original == replayed, "original_hash": original, "replayed_hash": replayed,
                "sent": len(stream), "stored": report["stored"], "duplicates_ignored": report["duplicates"],
                "first_ten_order": [p["event_id"][:8] for p in stream[:10]]}

    # ------------------------------------------------------------------- write

    def run(self, shop_id: str, now: str | None = None) -> dict:
        """Evaluate the policy and create drafts. Safe to call any number of times."""
        now_dt = self._now(now)
        ctx = self._context(shop_id, now_dt)
        session = ctx.session
        closed = {qid for qid, s in ctx.states.items() if s.closed}
        expired = workflow.expire_for_closed_quotes(session, closed)
        with session.tx():
            for b in ctx.baselines.values():
                session.run(
                    "INSERT INTO baselines (shop_id, metric, value, sample_n, source, computed_at) "
                    "VALUES (:shop_id, :metric, :value, :n, :source, :at) "
                    "ON CONFLICT (shop_id, metric) DO UPDATE SET value = excluded.value, "
                    "sample_n = excluded.sample_n, source = excluded.source, computed_at = excluded.computed_at",
                    metric=b.metric, value=b.value, n=b.sample_n, source=b.source, at=iso(now_dt),
                )
        created, blocked = [], []
        baseline_snapshot = {k: b.to_dict() for k, b in ctx.baselines.items()}
        for cand in ctx.result.candidates:
            c = cand.to_dict()
            tctx = self._text_context(ctx, c)
            body, author, attempts = self._write(self._writer_input(ctx, c), tctx)
            for a in attempts:
                self.guard_log.append({"quote_id": c["quote_id"], "at": iso(now_dt), **a})
            if body is None:
                blocked.append({"quote_id": c["quote_id"], "attempts": attempts})
                log.error("no acceptable text for quote %s", c["quote_id"])
                continue
            with session.tx():
                draft_id = workflow.create_draft(session, c, body, author, tctx, now_dt)
                if draft_id is None:
                    continue
                decision_log.record(
                    session, draft_id, c["quote_id"], now_dt, c["signals"], baseline_snapshot,
                    {"tier": c["tier"], "strategy": c["strategy"], "framing": (c.get("framing") or {}).get("kind"),
                     "offer": c.get("offer"), "writer": author,
                     "capacity": {"slack": ctx.capacity.shop_slack, "connected": ctx.capacity.connected}},
                )
            created.append({"draft_id": draft_id, "quote_id": c["quote_id"], "written_by": author,
                            "fell_back": bool(attempts)})
        return {"created": created, "blocked": blocked, "expired": expired,
                "candidates": len(ctx.result.candidates)}

    def approve(self, shop_id: str, draft_id: str, actor: str = "user:owner", role: str = "owner",
                now: str | None = None) -> dict:
        return self._draft_out(workflow.approve(self.session(shop_id), draft_id, actor, role, self._now(now)))

    def edit(self, shop_id: str, draft_id: str, body: str, actor: str = "user:owner", now: str | None = None) -> dict:
        return self._draft_out(workflow.edit(self.session(shop_id), draft_id, body, actor, self._now(now)))

    def reject(self, shop_id: str, draft_id: str, actor: str = "user:owner", now: str | None = None) -> dict:
        return self._draft_out(workflow.reject(self.session(shop_id), draft_id, actor, self._now(now)))

    def send(self, shop_id: str, draft_id: str, now: str | None = None) -> dict:
        return delivery.send(self.session(shop_id), draft_id, self._now(now), self.sender,
                             self.configs[shop_id], self.capacity, self.global_sending_enabled)

    def reconcile(self, shop_id: str, draft_id: str, now: str | None = None) -> dict:
        return delivery.reconcile(self.session(shop_id), draft_id, self._now(now), self.sender)

    def add_quotes(self, shop_id: str, quotes: list[dict]) -> dict:
        """New or refreshed quotes, as the quoting system would send them. Same loader as the seed."""
        return {"quotes": load_quotes(self.session(shop_id), quotes)}

    def ingest(self, shop_id: str, events: list[Any]) -> dict:
        return ingest_events(self.session(shop_id), events).to_dict()

    def accept_quote(self, shop_id: str, quote_id: str, now: str | None = None) -> dict:
        """The customer accepts. Arrives as an ordinary event, like any webhook."""
        return self.ingest(shop_id, [{"event_id": "demo-" + uuid.uuid4().hex[:12], "type": "quote_accepted",
                                      "quote_id": quote_id, "timestamp": iso(self._now(now))}])

    # ---------------------------------------------------------------- controls

    def set_sending(self, shop_id: str, enabled: bool) -> dict:
        self.session(shop_id).run("UPDATE shops SET sending_enabled = :v WHERE shop_id = :shop_id",
                                  v=1 if enabled else 0)
        return {"shop": bool(enabled)}

    def set_global_sending(self, enabled: bool) -> dict:
        self.global_sending_enabled = bool(enabled)
        return {"global": self.global_sending_enabled}

    def set_capacity(self, shop_id: str, booked_hours: float | None = None, median_booked_hours: float | None = None,
                     available_hours: float | None = None, openings: list[dict] | None = None,
                     fail: bool = False) -> dict:
        self.session(shop_id)
        self.capacity.fail = bool(fail)
        self.capacity.set(shop_id, booked_hours, median_booked_hours, available_hours, openings)
        return {"ok": True}

    def fill_slot(self, shop_id: str, slot_id: str) -> dict:
        self.session(shop_id)
        self.capacity.fill_slot(shop_id, slot_id)
        return {"ok": True}

    def update_config(self, shop_id: str, changes: dict) -> dict:
        self.session(shop_id)
        bad = set(changes) - CONFIG_EDITABLE
        if bad:
            raise WorkflowError("bad_config", "not an editable setting: " + ", ".join(sorted(bad)))
        types = {f.name: f.type for f in fields(ShopConfig)}
        clean = {}
        for key, value in changes.items():
            kind = types[key]
            clean[key] = bool(value) if kind == "bool" else int(value) if kind == "int" else float(value)
        self.configs[shop_id] = self.configs[shop_id].with_(**clean)
        return {k: getattr(self.configs[shop_id], k) for k in sorted(clean)}

    def script_sender(self, steps: list[str]) -> dict:
        """Tell the stand-in provider how to answer the next sends: accept, reject, timeout."""
        self.sender.script = list(steps)
        return {"script": list(steps)}

    def set_writer(self, mode: str = "template", text: str | None = None) -> dict:
        """Swap the writer. `hostile` stands in for a model that returns the given text."""
        if mode == "template":
            self.writer = TemplateWriter()
        elif mode == "hostile":
            template = text or ""

            def render(inp: WriterInput) -> str:
                from .agents.base import fmt_money
                return (template.replace("{first}", inp.customer_first).replace("{tech}", inp.tech_first)
                        .replace("{amount}", fmt_money(inp.amount_cents)).replace("{other}", "Mrs Okafor"))
            self.writer = FixedWriter(render, name="hostile-model")
        elif mode == "broken":
            self.writer = FixedWriter(error=TimeoutError("model timed out"), name="broken-model")
        else:
            raise WorkflowError("bad_writer", "unknown writer mode")
        return {"writer": self.writer.name}

    # ------------------------------------------------------------------- guard

    def hostile_cases(self) -> list[dict]:
        return HOSTILE_CASES

    def guard_check(self, shop_id: str, text: str, draft_id: str | None = None, quote_id: str | None = None,
                    now: str | None = None) -> dict:
        """Run the text rules against a draft's allowed set, or a plain message for a quote."""
        session = self.session(shop_id)
        if draft_id:
            d = workflow.get_draft(session, draft_id)
            tctx = TextContext.from_dict(json.loads(d["allowed_json"]))
        else:
            ctx = self._context(shop_id, self._now(now))
            s = ctx.states.get(quote_id or "")
            if s is None:
                raise WorkflowError("not_found", "quote not found")
            cand = {"customer_name": s.customer_name, "tech_name": s.tech_name, "amount_cents": s.amount_cents,
                    "framing": None, "offer": None}
            tctx = self._text_context(ctx, cand)
        violations = [v.to_dict() for v in check_text(text, tctx)]
        return {"ok": not violations, "violations": violations, "allowed": tctx.to_dict()}

    def decisions(self, shop_id: str) -> list[dict]:
        return decision_log.read(self.session(shop_id))


PUBLIC = {
    "shops", "overview", "quote_detail", "state_hash", "messy_check", "run", "approve", "edit", "reject", "send",
    "reconcile", "ingest", "add_quotes", "accept_quote", "set_sending", "set_global_sending", "set_capacity", "fill_slot",
    "update_config", "script_sender", "set_writer", "hostile_cases", "guard_check", "decisions",
}


def call(engine: Engine, name: str, kwargs: dict | None = None) -> dict:
    """The single entry point both transports use. Errors come back as data."""
    if name not in PUBLIC:
        return {"ok": False, "error": {"code": "unknown_action", "message": "unknown action"}}
    try:
        return {"ok": True, "data": getattr(engine, name)(**(kwargs or {}))}
    except WorkflowError as exc:
        return {"ok": False, "error": {"code": exc.code, "message": exc.message}}
    except TenantScopeError:
        return {"ok": False, "error": {"code": "not_found", "message": "shop not found"}}
    except (TypeError, ValueError, KeyError) as exc:
        return {"ok": False, "error": {"code": "bad_request", "message": str(exc)}}

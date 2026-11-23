"""Filters, tiers and ranking.

Pure. Takes state, signals, thresholds, a few facts from the workflow tables and a
capacity snapshot. Returns candidates and, just as usefully, who was excluded and why.

`path` on each result records the questions asked and the answers, in the order of
the flowchart in docs/DESIGN.md section 9. The page uses it to light the diagram.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Mapping

from ..capacity.base import CapacitySnapshot
from ..platform.config import ShopConfig
from ..platform.timeutil import hours_between
from ..state.projector import QuoteState
from ..state.signals import Signals
from .offers import choose_strategy, decide_close
from .thresholds import Thresholds

TIER_CODES = {1: "awaiting_reply", 2: "hot_view", 3: "big_quiet", 4: "first_followup", 5: "stale"}


@dataclass(frozen=True)
class QuoteFacts:
    """What the workflow tables know about a quote."""
    snoozed_until: datetime | None = None
    had_costed_offer: bool = False
    prior_framings: frozenset[str] = frozenset()


@dataclass(frozen=True)
class CustomerFacts:
    has_live_draft: bool = False


@dataclass
class Candidate:
    quote_id: str
    customer_id: str
    customer_name: str
    tech_name: str
    amount_cents: int
    tier: int
    reason_code: str
    reason: str
    strategy: str
    framing: dict | None
    offer: dict | None
    approver_note: str
    requires_owner: bool
    signals: dict
    path: list[list[str]]
    rank: int = 0

    def to_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class Excluded:
    quote_id: str
    customer_id: str
    customer_name: str
    amount_cents: int
    why: str
    detail: str
    path: list[list[str]]
    manual_lead: bool = False

    def to_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class PolicyResult:
    candidates: list[Candidate] = field(default_factory=list)
    excluded: list[Excluded] = field(default_factory=list)


def _ago(hours: float) -> str:
    if hours < 48:
        return f"{hours:.0f} hours"
    return f"{hours / 24:.1f} days"


def _tier(sig: Signals, big: bool, th: Thresholds, path: list[list[str]]) -> tuple[int | None, str]:
    """Walk the tier questions. Returns the tier, or None with the reason."""
    path.append(["T1", "yes" if sig.awaiting_our_reply else "no"])
    if sig.awaiting_our_reply:
        return 1, ""
    path.append(["T2", "yes" if sig.viewed_since_contact else "no"])
    if sig.viewed_since_contact:
        return 2, ""
    attempts = sig.unanswered_attempts
    served = attempts == 0 or sig.hours_since_contact >= th.backoff_h(attempts)
    path.append(["BK", "yes" if served else "no"])
    if not served:
        return None, (f"backoff: {attempts} unanswered, waiting {_ago(th.backoff_h(attempts))}, "
                      f"{_ago(sig.hours_since_contact)} served")
    t3 = big and sig.hours_since_contact >= th.big_quiet_h
    path.append(["T3", "yes" if t3 else "no"])
    if t3:
        return 3, ""
    t4 = sig.never_contacted and sig.age_hours >= th.first_followup_h
    path.append(["T4", "yes" if t4 else "no"])
    if t4:
        return 4, ""
    t5 = sig.hours_since_contact >= th.stale_h
    path.append(["T5", "yes" if t5 else "no"])
    if t5:
        return 5, ""
    return None, "not due yet"


def _reason(tier: int, s: QuoteState, sig: Signals, th: Thresholds, now: datetime) -> str:
    ratio = f"{sig.amount_ratio:.1f} times the shop's typical quote"
    if tier == 1:
        waited = _ago(hours_between(s.last_reply, now))
        channel = f" by {sig.reply_channel}" if sig.reply_channel else ""
        return f"Customer replied{channel} {waited} ago and nobody has answered."
    if tier == 2:
        seen = _ago(hours_between(s.last_view, now))
        parts = [f"Viewed {sig.views_since_contact} time{'s' if sig.views_since_contact != 1 else ''} "
                 f"since our last contact, most recently {seen} ago."]
        if sig.view_burst:
            parts.append(f"{th.burst_views} or more views inside one typical gap ({_ago(th.burst_window_h)}).")
        if sig.returned_after_gap:
            parts.append("Came back after a long silence.")
        parts.append(f"Quote is {ratio}.")
        return " ".join(parts)
    if tier == 3:
        text = f"Big quote, {ratio}, with no contact for {_ago(sig.hours_since_contact)}."
        if sig.views_since_contact:
            text += f" Viewed {sig.views_since_contact} time{'s' if sig.views_since_contact != 1 else ''} since."
        return text
    if tier == 4:
        return (f"Never contacted and {_ago(sig.age_hours)} old. "
                f"This shop usually makes first contact within {_ago(th.first_followup_h)}.")
    return (f"No contact for {_ago(sig.hours_since_contact)}. "
            f"Quotes here usually close within {_ago(th.stale_h)}.")


def evaluate(
    states: Mapping[str, QuoteState],
    signals: Mapping[str, Signals],
    th: Thresholds,
    config: ShopConfig,
    now: datetime,
    quote_facts: Mapping[str, QuoteFacts] | None = None,
    customer_facts: Mapping[str, CustomerFacts] | None = None,
    capacity: CapacitySnapshot | None = None,
    budget_spent_cents: int = 0,
) -> PolicyResult:
    quote_facts = quote_facts or {}
    customer_facts = customer_facts or {}
    capacity = capacity or CapacitySnapshot()
    result = PolicyResult()

    # Customer level contact facts span all of a customer's quotes, open or closed.
    last_contact: dict[str, datetime] = {}
    last_reply: dict[str, datetime] = {}
    for s in states.values():
        if s.last_contact and s.last_contact > last_contact.get(s.customer_id, datetime.min.replace(tzinfo=now.tzinfo)):
            last_contact[s.customer_id] = s.last_contact
        if s.last_reply and s.last_reply > last_reply.get(s.customer_id, datetime.min.replace(tzinfo=now.tzinfo)):
            last_reply[s.customer_id] = s.last_reply

    def exclude(s: QuoteState, why: str, detail: str, path, manual_lead: bool = False) -> None:
        result.excluded.append(Excluded(s.quote_id, s.customer_id, s.customer_name, s.amount_cents,
                                        why, detail, path, manual_lead))

    eligible: list[tuple[tuple, QuoteState, Signals, int, list]] = []
    for qid in sorted(states):
        s = states[qid]
        sig = signals[qid]
        facts = quote_facts.get(qid, QuoteFacts())
        path: list[list[str]] = []
        big = s.amount_cents >= th.big_amount_cents

        # Question B: closed, held, or attempts exhausted.
        if s.closed:
            path.append(["B", "yes"])
            revisited = bool(s.last_view) and hours_between(s.last_view, now) <= th.hot_window_h
            exclude(s, "closed", f"quote is {s.status}" + (", viewed again recently" if revisited else ""),
                    path, manual_lead=revisited and s.status == "dismissed")
            continue
        if sig.hold:
            path.append(["B", "yes"])
            detail = "quote is less than a day old" if sig.hold == "fresh" else "we answered their reply recently"
            exclude(s, "hold_" + sig.hold, detail, path)
            continue
        if facts.snoozed_until and facts.snoozed_until > now:
            path.append(["B", "yes"])
            exclude(s, "snoozed", "a draft was rejected recently", path)
            continue
        if sig.unanswered_attempts >= config.attempt_cap and not sig.awaiting_our_reply:
            path.append(["B", "yes"])
            exclude(s, "gone_quiet", f"{sig.unanswered_attempts} unanswered messages, listed for the owner", path)
            continue
        path.append(["B", "no"])

        # Question C: customer cooldown or a live draft.
        c_contact = last_contact.get(s.customer_id)
        c_reply = last_reply.get(s.customer_id)
        replied_since = c_reply is not None and (c_contact is None or c_reply > c_contact)
        in_cooldown = (
            c_contact is not None
            and hours_between(c_contact, now) < config.cooldown_hours
            and not replied_since   # a reply clears the cooldown
        )
        if in_cooldown:
            path.append(["C", "yes"])
            exclude(s, "cooldown", f"customer contacted {_ago(hours_between(c_contact, now))} ago", path)
            continue
        if customer_facts.get(s.customer_id, CustomerFacts()).has_live_draft:
            path.append(["C", "yes"])
            exclude(s, "live_draft", "customer already has a draft in review", path)
            continue
        path.append(["C", "no"])

        tier, why_not = _tier(sig, big, th, path)
        if tier is None:
            exclude(s, why_not.split(":")[0].replace(" ", "_"), why_not, path)
            continue

        deprioritised = capacity.fully_booked and not big and tier >= 3
        latest = max([t for t in (s.last_view, s.last_reply, s.last_contact, s.created_at) if t])
        key = (tier, deprioritised, not sig.view_burst, -sig.amount_ratio, -latest.timestamp(), qid)
        eligible.append((key, s, sig, tier, path))

    # One candidate per customer. The best tier wins.
    eligible.sort(key=lambda item: item[0])
    chosen: set[str] = set()
    budget_left = max(0, config.monthly_offer_budget_cents - budget_spent_cents)
    for key, s, sig, tier, path in eligible:
        if s.customer_id in chosen:
            path.append(["M", "other"])
            exclude(s, "other_quote_chosen", "the customer's other quote ranked higher", path)
            continue
        chosen.add(s.customer_id)
        path.append(["M", "chosen"])
        facts = quote_facts.get(s.quote_id, QuoteFacts())
        big = s.amount_cents >= th.big_amount_cents
        strategy = choose_strategy(tier, sig, big, bool(s.views))

        framing = offer = None
        note = ""
        if strategy == "reply":
            note = "Needs a human reply. We do not know what the customer said. Edit before approving."
        elif strategy == "close":
            decision = decide_close(
                s.amount_cents, sig.unanswered_attempts, s.customer_id, facts.had_costed_offer,
                facts.prior_framings, capacity, budget_left, config, now,
            )
            framing, offer, note = decision.framing.to_dict(), decision.offer, decision.note
            if offer is not None and offer.costed:
                budget_left -= offer.value_cents

        result.candidates.append(Candidate(
            quote_id=s.quote_id, customer_id=s.customer_id, customer_name=s.customer_name,
            tech_name=s.tech_name, amount_cents=s.amount_cents, tier=tier,
            reason_code=TIER_CODES[tier], reason=_reason(tier, s, sig, th, now),
            strategy=strategy, framing=framing, offer=offer.to_dict() if offer else None,
            approver_note=note, requires_owner=bool(offer and offer.costed),
            signals=sig.to_dict(), path=path, rank=len(result.candidates) + 1,
        ))
    return result

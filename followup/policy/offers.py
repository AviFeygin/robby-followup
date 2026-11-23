"""Strategy ladder and offer choice.

The engine never invents a number. Offer sizes come from the shop's config, scale
with capacity, and are clamped by the shop's cap and monthly budget.
"""
from __future__ import annotations

import math

from dataclasses import dataclass
from datetime import datetime, timedelta

from ..capacity.base import CapacitySnapshot, Opening
from ..platform.config import ShopConfig
from ..platform.timeutil import iso, parse
from ..state.signals import Signals
from .framing import Framing, choose_premium, premium_framing, price_hold_framing, was_now_framing

ROUND_TO_CENTS = 2500  # offers are quoted in steps of $25


@dataclass(frozen=True)
class Offer:
    kind: str                    # price_hold, percent_off
    value_cents: int
    expires_at: str
    slot_ref: str | None = None
    slot_label: str | None = None
    percent: float | None = None

    @property
    def costed(self) -> bool:
        return self.value_cents > 0

    def to_dict(self) -> dict:
        return {"kind": self.kind, "value_cents": self.value_cents, "expires_at": self.expires_at,
                "slot_ref": self.slot_ref, "slot_label": self.slot_label,
                "percent": round(self.percent, 4) if self.percent is not None else None,
                "costed": self.costed}


def choose_strategy(tier: int, sig: Signals, big: bool, has_views: bool) -> str:
    if tier == 1:
        return "reply"
    attempts = sig.unanswered_attempts
    if sig.never_viewed and attempts < 2:
        return "check_delivery"
    if attempts == 0:
        return "nudge"
    if big and has_views:
        return "close"
    return "help"


def effective_slack(snapshot: CapacitySnapshot, opening: Opening | None, config: ShopConfig) -> float:
    if not config.capacity_pricing_enabled:
        return 0.0
    slack = snapshot.shop_slack + (config.nearby_step if opening else 0.0)
    return max(0.0, min(1.0, slack))


def slack_curve(slack: float, config: ShopConfig) -> float:
    """Logistic response to slack, rescaled so slack 0 maps to 0 and slack 1 maps to 1.

    Near a median week and near an empty week the offer changes little. It changes fastest
    around slack_curve_mid. Steepness 0 gives a straight line.
    """
    x = max(0.0, min(1.0, slack))
    k, mid = config.slack_curve_steepness, config.slack_curve_mid
    if k <= 0:
        return x
    def f(t: float) -> float:
        return 1.0 / (1.0 + math.exp(-k * (t - mid)))
    lo, hi = f(0.0), f(1.0)
    return max(0.0, min(1.0, (f(x) - lo) / (hi - lo)))


def percent_value_cents(amount_cents: int, slack: float, config: ShopConfig) -> tuple[int, float]:
    """Offer size: base to max percent along the slack curve, then the cap, then round down to $25."""
    percent = config.percent_base + (config.percent_max - config.percent_base) * slack_curve(slack, config)
    value = min(int(amount_cents * percent), config.percent_cap_cents)
    value = (value // ROUND_TO_CENTS) * ROUND_TO_CENTS
    return value, percent


@dataclass(frozen=True)
class CloseDecision:
    framing: Framing
    offer: Offer | None
    note: str


def decide_close(
    amount_cents: int,
    attempts: int,
    customer_id: str,
    had_costed_offer: bool,
    prior_framings: frozenset[str],
    snapshot: CapacitySnapshot,
    budget_left_cents: int,
    config: ShopConfig,
    now: datetime,
) -> CloseDecision:
    """Pick a framing, and an offer if one is allowed. Free framings come first."""
    hold_until = now + timedelta(days=config.hold_days)
    opening = snapshot.opening_near(customer_id) if config.capacity_pricing_enabled else None
    slack = effective_slack(snapshot, opening, config)
    final_touch = attempts >= config.attempt_cap - 1
    early = opening is not None or (config.capacity_pricing_enabled and snapshot.shop_slack >= config.early_offer_slack)

    costed_allowed = (
        config.percent_off_enabled
        and not snapshot.fully_booked
        and not had_costed_offer
        and (final_touch or early)
    )
    if costed_allowed:
        value, percent = percent_value_cents(amount_cents, slack, config)
        if 0 < value <= budget_left_cents:
            expires = hold_until
            if opening is not None:
                expires = min(expires, parse(opening.ends_at))
            offer = Offer("percent_off", value, iso(expires), opening.slot_id if opening else None,
                          opening.label if opening else None, percent)
            why = "nearby opening" if opening else ("light week" if early else "final touch")
            return CloseDecision(
                was_now_framing(amount_cents, value), offer,
                f"Costed offer of ${value // 100:,} ({why}). Owner approval required.",
            )

    addon = choose_premium(amount_cents, config)
    if addon is not None and "premium_option" not in prior_framings:
        return CloseDecision(
            premium_framing(amount_cents, addon), None,
            f"Premium option anchor: {addon.label}. It is a real package, sell it if they say yes.",
        )

    return CloseDecision(
        price_hold_framing(amount_cents),
        Offer("price_hold", 0, iso(hold_until)),
        "Price hold, no cost to the shop.",
    )

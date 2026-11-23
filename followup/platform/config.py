"""Per shop configuration.

Two kinds of numbers live here and they are kept apart on purpose.

Limits are promises a shop makes (cooldown, attempt cap, caps on offers). They are
fixed values and never derived from data.

Multipliers turn a shop's own medians into thresholds (see policy/thresholds.py).
They are shared by all shops. The scale comes from each shop's data.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace


@dataclass(frozen=True)
class AddOn:
    """A real, sellable add-on used for the premium option framing."""
    id: str
    label: str
    price_cents: int
    bundle_saving_cents: int = 0
    job_types: tuple[str, ...] = ()  # empty means it fits any job


# Starting values used only when neither the shop nor its parent has enough history.
PLATFORM_BASELINES: dict[str, float] = {
    "amount": 500000.0,        # cents
    "first_view": 48.0,        # hours
    "first_contact": 48.0,     # hours
    "view_gap": 48.0,          # hours
    "view_to_reply": 12.0,     # hours
    "time_to_accept": 168.0,   # hours, 7 days
    "utilisation": 0.8,        # share of available hours booked
}


@dataclass(frozen=True)
class ShopConfig:
    shop_name: str = "the shop"
    utc_offset_hours: int = -4  # a zone database is the production answer

    # Limits. Fixed promises, not statistics.
    cooldown_hours: float = 72.0
    attempt_cap: int = 3
    fresh_hold_hours: float = 24.0
    live_conversation_hold_hours: float = 48.0
    quiet_hours_enabled: bool = True
    quiet_start_hour: int = 9
    quiet_end_hour: int = 20
    daily_send_cap: int = 50

    # Baselines.
    min_samples: int = 15
    baseline_window_days: int = 90

    # Multipliers applied to the shop's medians.
    big_multiple: float = 2.0
    burst_views: int = 3
    returned_gap_multiple: float = 2.0
    big_quiet_fraction: float = 0.5

    # Offers. The engine never invents a number, it reads these.
    hold_days: int = 7
    percent_off_enabled: bool = True
    percent_base: float = 0.03
    percent_max: float = 0.06
    percent_cap_cents: int = 100000
    monthly_offer_budget_cents: int = 400000
    slack_curve_mid: float = 0.5        # slack where the offer rises fastest
    slack_curve_steepness: float = 8.0  # 0 is a straight line; higher is a sharper S
    nearby_step: float = 0.5           # a nearby opening counts as this much extra slack
    early_offer_slack: float = 0.5     # slack at which a costed offer may come one touch early
    capacity_pricing_enabled: bool = True

    # Premium option framing.
    addons: tuple[AddOn, ...] = ()
    premium_min_ratio: float = 1.2
    premium_max_ratio: float = 1.5

    # Text rules.
    blocked_words: tuple[str, ...] = ("guaranteed", "guarantee", "free", "promise")
    max_body_chars: int = 320
    tone: str = "friendly and brief"

    def with_(self, **changes) -> "ShopConfig":
        return replace(self, **changes)


def demo_config() -> ShopConfig:
    """Config for the seed shop. The offer and add-on values are our assumptions."""
    return ShopConfig(
        shop_name="Robby Home Services",
        addons=(
            AddOn(id="plan2", label="2 year service plan", price_cents=120000),
            AddOn(id="plan5", label="5 year service plan", price_cents=350000),
            AddOn(id="upgrade", label="high efficiency upgrade and 10 year warranty", price_cents=650000),
        ),
    )

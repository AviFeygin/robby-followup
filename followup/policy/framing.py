"""Price anchoring.

A framing changes how the price reads without changing it. Every number in a framing
comes from the quote, an offer or the shop's catalogue. A framing whose data is
missing is never chosen and never approximated.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..platform.config import AddOn, ShopConfig


@dataclass(frozen=True)
class Framing:
    kind: str                                  # price_hold, was_now, premium_option
    numbers_cents: dict[str, int] = field(default_factory=dict)
    addon_id: str | None = None
    addon_label: str | None = None

    def to_dict(self) -> dict:
        return {"kind": self.kind, "numbers_cents": dict(self.numbers_cents),
                "addon_id": self.addon_id, "addon_label": self.addon_label}


def package_price(amount_cents: int, addon: AddOn) -> int:
    return amount_cents + addon.price_cents - addon.bundle_saving_cents


def choose_premium(amount_cents: int, config: ShopConfig, job_type: str | None = None) -> AddOn | None:
    """Pick the add-on whose package lands in the anchoring range, nearest its middle.

    Too close to the quote and it does not anchor. Too far and it is ignored.
    """
    if amount_cents <= 0:
        return None
    middle = (config.premium_min_ratio + config.premium_max_ratio) / 2
    best: tuple[float, AddOn] | None = None
    for addon in config.addons:
        if addon.job_types and job_type not in addon.job_types:
            continue
        ratio = package_price(amount_cents, addon) / amount_cents
        if config.premium_min_ratio <= ratio <= config.premium_max_ratio:
            distance = abs(ratio - middle)
            if best is None or distance < best[0]:
                best = (distance, addon)
    return best[1] if best else None


def premium_framing(amount_cents: int, addon: AddOn) -> Framing:
    numbers = {"quote": amount_cents, "package": package_price(amount_cents, addon), "addon": addon.price_cents}
    if addon.bundle_saving_cents:
        numbers["bundle_saving"] = addon.bundle_saving_cents
    return Framing("premium_option", numbers, addon.id, addon.label)


def price_hold_framing(amount_cents: int) -> Framing:
    return Framing("price_hold", {"quote": amount_cents})


def was_now_framing(amount_cents: int, off_cents: int) -> Framing:
    return Framing("was_now", {"quote": amount_cents, "off": off_cents, "now": amount_cents - off_cents})

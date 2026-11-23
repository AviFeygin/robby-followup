"""Per shop medians.

Thresholds are derived from these, so a shop selling small repairs and a shop selling
large installs get different lines without anyone setting them.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import median
from typing import Iterable, Mapping

from ..platform.timeutil import hours_between
from .projector import QuoteState

METRICS = ("amount", "first_view", "first_contact", "view_gap", "view_to_reply", "time_to_accept")


@dataclass(frozen=True)
class Baseline:
    metric: str
    value: float
    sample_n: int
    source: str            # shop, parent, platform
    shop_value: float | None = None   # the shop's own median even when it was not used

    def to_dict(self) -> dict:
        return {"metric": self.metric, "value": self.value, "sample_n": self.sample_n,
                "source": self.source, "shop_value": self.shop_value}


def samples(states: Iterable[QuoteState], now: datetime, window_days: int) -> dict[str, list[float]]:
    """Raw samples per metric, from quotes created inside the trailing window."""
    cutoff = now - timedelta(days=window_days)
    out: dict[str, list[float]] = {m: [] for m in METRICS}
    for s in states:
        if s.created_at < cutoff:
            continue
        out["amount"].append(float(s.amount_cents))
        if s.views:
            out["first_view"].append(hours_between(s.created_at, s.views[0]))
            out["view_gap"].extend(hours_between(a, b) for a, b in zip(s.views, s.views[1:]))
        if s.contacts:
            out["first_contact"].append(hours_between(s.created_at, s.contacts[0]))
        if s.replies:
            first_reply = s.replies[0][0]
            prior_views = [v for v in s.views if v <= first_reply]
            if prior_views:
                out["view_to_reply"].append(hours_between(prior_views[-1], first_reply))
        if s.accepted_at:
            out["time_to_accept"].append(hours_between(s.created_at, s.accepted_at))
    return out


def compute_baselines(
    states: Iterable[QuoteState],
    now: datetime,
    min_samples: int,
    window_days: int,
    platform: Mapping[str, float],
    parent: Mapping[str, Baseline] | None = None,
) -> dict[str, Baseline]:
    """Median per metric. Too few samples falls back to the parent, then the platform."""
    data = samples(states, now, window_days)
    result: dict[str, Baseline] = {}
    for metric in METRICS:
        xs = data[metric]
        own = float(median(xs)) if xs else None
        if len(xs) >= min_samples:
            result[metric] = Baseline(metric, own, len(xs), "shop", own)
        elif parent and metric in parent and parent[metric].sample_n >= min_samples:
            result[metric] = Baseline(metric, parent[metric].value, len(xs), "parent", own)
        else:
            result[metric] = Baseline(metric, float(platform[metric]), len(xs), "platform", own)
    return result

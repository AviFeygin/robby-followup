"""Turn a shop's medians into thresholds.

Each threshold is a baseline times a shared multiplier. The table in docs/DESIGN.md
section 9 mirrors this file.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Mapping

from ..platform.config import ShopConfig
from ..state.baselines import Baseline
from ..state.signals import Windows


@dataclass(frozen=True)
class Thresholds:
    big_amount_cents: float      # big quote: a multiple of the median amount
    hot_window_h: float          # a view stays hot for one typical gap between views
    burst_window_h: float        # several views inside one typical gap is a burst
    burst_views: int
    returned_gap_h: float        # silence longer than this, then a view, is a return
    never_viewed_h: float        # no view by the typical time to a first view
    first_followup_h: float      # never contacted past the shop's own usual first contact
    stale_h: float               # no contact for longer than the typical time to accept
    big_quiet_h: float           # big quotes wait a fraction of the stale time
    backoff_step_h: float        # extra wait per unanswered attempt
    cooldown_h: float            # a limit, copied here so one object explains a decision

    def backoff_h(self, attempts: int) -> float:
        """Wait required before the next message after `attempts` unanswered ones."""
        return self.cooldown_h + attempts * self.backoff_step_h

    def to_dict(self) -> dict:
        d = {k: round(v, 1) if isinstance(v, float) else v for k, v in asdict(self).items()}
        d["backoff_h"] = [round(self.backoff_h(n), 1) for n in (1, 2)]
        return d

    def windows(self, config: ShopConfig, median_amount_cents: float) -> Windows:
        return Windows(
            hot_window=self.hot_window_h,
            burst_window=self.burst_window_h,
            burst_views=self.burst_views,
            returned_gap=self.returned_gap_h,
            never_viewed_after=self.never_viewed_h,
            fresh_hold=config.fresh_hold_hours,
            live_conversation_hold=config.live_conversation_hold_hours,
            median_amount_cents=median_amount_cents,
        )


def build_thresholds(baselines: Mapping[str, Baseline], config: ShopConfig) -> Thresholds:
    b = {k: v.value for k, v in baselines.items()}
    return Thresholds(
        big_amount_cents=config.big_multiple * b["amount"],
        hot_window_h=b["view_gap"],
        burst_window_h=b["view_gap"],
        burst_views=config.burst_views,
        returned_gap_h=config.returned_gap_multiple * b["view_gap"],
        never_viewed_h=b["first_view"],
        first_followup_h=b["first_contact"],
        stale_h=b["time_to_accept"],
        big_quiet_h=config.big_quiet_fraction * b["time_to_accept"],
        backoff_step_h=b["view_gap"],
        cooldown_h=config.cooldown_hours,
    )

"""The writer contract.

WriterInput is the complete list of what a writer may see. There is no phone number,
no customer id, no other customer and no other shop in it. A writer returns text and
nothing else is read from it: not a recipient, not a channel, not a price.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol

from ..platform.timeutil import parse


@dataclass(frozen=True)
class WriterInput:
    customer_first: str
    tech_first: str
    shop_name: str
    amount_cents: int
    reason_code: str
    strategy: str
    tone: str
    framing: dict | None = None
    offer: dict | None = None
    expires_label: str | None = None     # customer facing date, such as "Aug 24"
    reply_text: str | None = None        # untrusted: what the customer wrote, tier 1 only


class Writer(Protocol):
    name: str

    def write(self, inp: WriterInput) -> str: ...


def fmt_money(cents: int) -> str:
    dollars, rem = divmod(int(cents), 100)
    return f"${dollars:,}" if rem == 0 else f"${dollars:,}.{rem:02d}"


def fmt_date(iso_time: str, utc_offset_hours: int = 0) -> str:
    local = parse(iso_time) + timedelta(hours=utc_offset_hours)
    return f"{local.strftime('%b')} {local.day}"


def first_name(full: str) -> str:
    return (full or "").strip().split(" ")[0] or "there"

"""Template writer. The default, and the fallback whenever a model draft is rejected."""
from __future__ import annotations

from .base import WriterInput, fmt_money


class TemplateWriter:
    name = "template"

    def write(self, inp: WriterInput) -> str:
        hi = f"Hi {inp.customer_first}, it's {inp.tech_first} at {inp.shop_name}."
        amount = fmt_money(inp.amount_cents)
        framing = inp.framing or {}
        offer = inp.offer or {}
        numbers = framing.get("numbers_cents", {})

        if inp.strategy == "reply":
            return f"{hi} Thanks for your message about the {amount} quote. I'll get back to you shortly."
        if inp.strategy == "check_delivery":
            return f"{hi} I sent over your quote for {amount} and wanted to make sure it reached you. Want me to resend it?"
        if inp.strategy == "nudge":
            return f"{hi} Just checking in on the quote for {amount}. Any questions I can answer?"
        if inp.strategy == "help":
            return (f"{hi} Happy to walk through the {amount} quote on a quick call, "
                    f"or answer anything by text. What works for you?")

        # close
        kind = framing.get("kind")
        if kind == "was_now":
            off, now_price = fmt_money(numbers["off"]), fmt_money(numbers["now"])
            if offer.get("slot_label"):
                return (f"{hi} I have an opening {offer['slot_label']} in your area and can take {off} off "
                        f"your {amount} quote if we book it. That makes it {now_price}. Want the spot?")
            return (f"{hi} Your quote was {amount}. I can do {now_price} if we book by {inp.expires_label}. "
                    f"Want me to hold a spot?")
        if kind == "premium_option":
            return (f"{hi} The full package with the {framing['addon_label']} is {fmt_money(numbers['package'])}. "
                    f"The work we quoted on its own is {amount}. Happy to go either way. Which suits you?")
        return (f"{hi} I can hold your quote of {amount} at that price until {inp.expires_label}. "
                f"Want me to get you on the schedule?")

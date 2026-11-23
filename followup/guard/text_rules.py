"""Checks on proposed message text. Pure.

The rules do not trust the writer. Any dollar figure, percent or date in the text
must be one the policy engine put on the draft. That holds for a template, a model,
or a model that has been talked into something by a customer's reply.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]

_MONEY = re.compile(r"\$\s?(\d[\d,]*(?:\.\d{1,2})?)(?:\s?([kK])\b)?")
_DOLLAR_WORDS = re.compile(r"\b(\d[\d,]*(?:\.\d{1,2})?)(?:\s?([kK])\b)?\s?(?:dollars|bucks|usd)\b", re.I)
_PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s?(?:%|percent\b|pct\b)", re.I)
_DATE_NAMED = re.compile(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})\b", re.I)
_DATE_NUMERIC = re.compile(r"\b(\d{1,2})/(\d{1,2})(?:/\d{2,4})?\b")
_DISCOUNT = re.compile(
    r"\b(discount\w*|coupon\w*|rebate\w*|sale price|half[- ]price|percent off|money off|"
    r"knock\w* \S+ off|take \S+ off|price cut|mark\w* down|cheaper)\b", re.I)
_URL = re.compile(r"(https?://|www\.|\b[\w-]+\.(?:com|net|org|io|co|ly|me|biz|info)\b)", re.I)
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.\w+")
_PHONE = re.compile(r"\+?\d[\d\-\s().]{7,}\d")
_BAD_CHARS = re.compile(r"[<>{}\[\]`\\]")


@dataclass(frozen=True)
class Violation:
    rule: str
    detail: str

    def to_dict(self) -> dict:
        return {"rule": self.rule, "detail": self.detail}


@dataclass(frozen=True)
class TextContext:
    """Everything the text rules compare against. Stored on the draft as allowed_json."""
    customer_first: str
    allowed_money_cents: tuple[int, ...] = ()
    allowed_dates: tuple[tuple[int, int], ...] = ()      # (month, day)
    has_costed_offer: bool = False
    forbidden_terms: tuple[str, ...] = ()                # other customers, schedule details
    blocked_words: tuple[str, ...] = ()
    max_chars: int = 320

    def to_dict(self) -> dict:
        return {
            "customer_first": self.customer_first,
            "allowed_money_cents": list(self.allowed_money_cents),
            "allowed_dates": [list(d) for d in self.allowed_dates],
            "has_costed_offer": self.has_costed_offer,
            "forbidden_terms": list(self.forbidden_terms),
            "blocked_words": list(self.blocked_words),
            "max_chars": self.max_chars,
        }

    @staticmethod
    def from_dict(d: dict) -> "TextContext":
        return TextContext(
            customer_first=d["customer_first"],
            allowed_money_cents=tuple(d.get("allowed_money_cents", ())),
            allowed_dates=tuple((int(m), int(day)) for m, day in d.get("allowed_dates", ())),
            has_costed_offer=bool(d.get("has_costed_offer")),
            forbidden_terms=tuple(d.get("forbidden_terms", ())),
            blocked_words=tuple(d.get("blocked_words", ())),
            max_chars=int(d.get("max_chars", 320)),
        )


def body_hash(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def build_context(
    customer_first: str,
    amount_cents: int,
    framing: dict | None,
    offer: dict | None,
    expires_month_day: tuple[int, int] | None,
    forbidden_terms: tuple[str, ...],
    blocked_words: tuple[str, ...],
    max_chars: int,
) -> TextContext:
    money = {int(amount_cents)}
    if framing:
        money.update(int(v) for v in framing.get("numbers_cents", {}).values())
    if offer and offer.get("value_cents"):
        money.add(int(offer["value_cents"]))
    return TextContext(
        customer_first=customer_first,
        allowed_money_cents=tuple(sorted(money)),
        allowed_dates=(expires_month_day,) if expires_month_day else (),
        has_costed_offer=bool(offer and offer.get("value_cents")),
        forbidden_terms=forbidden_terms,
        blocked_words=blocked_words,
        max_chars=max_chars,
    )


def _to_cents(number: str, k: str | None) -> int:
    value = float(number.replace(",", ""))
    if k:
        value *= 1000
    return int(round(value * 100))


def check_text(body: str, ctx: TextContext) -> list[Violation]:
    out: list[Violation] = []
    text = body or ""

    # Shape.
    if not text.strip():
        return [Violation("shape", "empty message")]
    if len(text) > ctx.max_chars:
        out.append(Violation("shape", f"{len(text)} characters, limit is {ctx.max_chars}"))
    if _BAD_CHARS.search(text) or any(ord(ch) < 32 and ch not in "\n" for ch in text):
        out.append(Violation("shape", "markup or control characters"))

    # Money. Every figure must be one the policy put on the draft.
    allowed = set(ctx.allowed_money_cents)
    for match in list(_MONEY.finditer(text)) + list(_DOLLAR_WORDS.finditer(text)):
        cents = _to_cents(match.group(1), match.group(2))
        if cents not in allowed:
            out.append(Violation("money", f"{match.group(0).strip()} is not a figure on this draft"))
    for match in _PERCENT.finditer(text):
        out.append(Violation("money", f"{match.group(0).strip()} is not a figure on this draft"))

    # Dates.
    dates = set(ctx.allowed_dates)
    for match in _DATE_NAMED.finditer(text):
        found = (MONTHS.index(match.group(1).lower()[:3]) + 1, int(match.group(2)))
        if found not in dates:
            out.append(Violation("dates", f"{match.group(0)} is not the expiry on this draft"))
    for match in _DATE_NUMERIC.finditer(text):
        found = (int(match.group(1)), int(match.group(2)))
        if found not in dates:
            out.append(Violation("dates", f"{match.group(0)} is not the expiry on this draft"))

    # Discount language with no offer behind it.
    if not ctx.has_costed_offer:
        match = _DISCOUNT.search(text)
        if match:
            out.append(Violation("no_offer_granted", f"'{match.group(0)}' but this draft has no offer"))

    # Contact details.
    if _URL.search(text):
        out.append(Violation("contact_details", "contains a link"))
    if _EMAIL.search(text):
        out.append(Violation("contact_details", "contains an email address"))
    for match in _PHONE.finditer(text):
        if len(re.sub(r"\D", "", match.group(0))) >= 7:
            out.append(Violation("contact_details", "contains a phone number"))
            break

    # Names and other jobs.
    if ctx.customer_first and not re.search(r"\b" + re.escape(ctx.customer_first) + r"\b", text):
        out.append(Violation("names", "does not address this customer by name"))
    for term in ctx.forbidden_terms:
        if term and re.search(r"\b" + re.escape(term) + r"\b", text):
            out.append(Violation("names", "names another customer or job"))
            break

    # Promises.
    for word in ctx.blocked_words:
        if re.search(r"\b" + re.escape(word) + r"\b", text, re.I):
            out.append(Violation("promises", f"'{word}' is on the shop's block list"))

    return out

"""Ingestion engine.

Takes raw webhook payloads or seed lines. Stores every valid event once.
Nothing here decides what an event means. That is the state module's job.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

from ..platform import eventlog
from ..platform.db import ShopSession
from ..platform.redact import get_logger
from ..platform.timeutil import iso, parse

log = get_logger("followup.ingestion")

KNOWN_TYPES = {"quote_sent", "quote_viewed", "customer_replied", "message_sent", "quote_accepted"}
CLOSED = {"accepted", "dismissed"}


@dataclass
class IngestReport:
    received: int = 0
    stored: int = 0
    duplicates: int = 0
    malformed: list[dict[str, Any]] = field(default_factory=list)
    unknown_types: list[str] = field(default_factory=list)
    unknown_quotes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalise_phone(raw: str) -> str:
    digits = re.sub(r"\D", "", raw or "")
    if not digits:
        raise ValueError("phone number has no digits")
    if len(digits) == 10:
        digits = "1" + digits
    return "+" + digits


def load_quotes(session: ShopSession, quotes: Iterable[dict[str, Any]]) -> int:
    """Load or refresh quotes. A closed quote never reopens."""
    count = 0
    with session.tx():
        for q in quotes:
            phone = normalise_phone(q["customer_phone"])
            customer_id = "C-" + phone.lstrip("+")
            session.run(
                "INSERT INTO customers (shop_id, id, phone_e164, name) VALUES (:shop_id, :id, :phone, :name) "
                "ON CONFLICT DO NOTHING",
                id=customer_id, phone=phone, name=q["customer_name"],
            )
            status = q.get("status", "open")
            if status not in CLOSED and status != "open":
                status = "open"
            created = iso(parse(q["created_at"]))
            last_contact = iso(parse(q["last_contact_at"])) if q.get("last_contact_at") else None
            inserted = session.run(
                "INSERT INTO quotes (shop_id, id, customer_id, tech_name, amount_cents, status, created_at, last_contact_at) "
                "VALUES (:shop_id, :id, :customer_id, :tech, :amount, :status, :created, :last_contact) "
                "ON CONFLICT DO NOTHING",
                id=q["id"], customer_id=customer_id, tech=q.get("tech_name", ""),
                amount=int(round(float(q["amount"]) * 100)), status=status,
                created=created, last_contact=last_contact,
            )
            if not inserted:
                # Refresh. Status only ever moves from open to closed.
                session.run(
                    "UPDATE quotes SET last_contact_at = :last_contact, "
                    "status = CASE WHEN status = 'open' THEN :status ELSE status END "
                    "WHERE shop_id = :shop_id AND id = :id",
                    id=q["id"], status=status, last_contact=last_contact,
                )
            count += 1
    return count


def _normalise(payload: dict[str, Any]) -> dict[str, Any]:
    event_id = payload.get("event_id")
    etype = payload.get("type")
    stamp = payload.get("timestamp")
    if not event_id or not isinstance(event_id, str):
        raise ValueError("missing event_id")
    if not etype or not isinstance(etype, str):
        raise ValueError("missing type")
    if not stamp:
        raise ValueError("missing timestamp")
    return {
        "event_id": event_id,
        "quote_id": payload.get("quote_id"),
        "type": etype,
        "occurred_at": iso(parse(stamp)),
        "channel": payload.get("channel"),
        "direction": payload.get("direction"),
        # The shop comes from the session, never from the payload.
        "raw": {k: v for k, v in payload.items() if k != "shop_id"},
    }


def ingest_events(session: ShopSession, payloads: Iterable[Any]) -> IngestReport:
    report = IngestReport()
    with session.tx():
        known_quotes = {r["id"] for r in session.rows("SELECT id FROM quotes WHERE shop_id = :shop_id")}
        for index, payload in enumerate(payloads, start=1):
            report.received += 1
            try:
                if not isinstance(payload, dict):
                    raise ValueError("event is not an object")
                event = _normalise(payload)
            except (ValueError, TypeError) as exc:
                report.malformed.append({"line": index, "error": str(exc)})
                log.warning("skipped malformed event at line %s: %s", index, exc)
                continue
            if eventlog.append(session, event):
                report.stored += 1
                if event["type"] not in KNOWN_TYPES and event["type"] not in report.unknown_types:
                    report.unknown_types.append(event["type"])
                if event["quote_id"] not in known_quotes and event["quote_id"] not in report.unknown_quotes:
                    report.unknown_quotes.append(str(event["quote_id"]))
            else:
                report.duplicates += 1
    return report


def ingest_lines(session: ShopSession, lines: Iterable[str]) -> IngestReport:
    """Ingest JSON lines. One bad line does not stop the load."""
    payloads: list[Any] = []
    for line in lines:
        text = line.strip()
        if not text:
            continue
        try:
            payloads.append(json.loads(text))
        except json.JSONDecodeError:
            payloads.append(None)  # reported as malformed with its position
    return ingest_events(session, payloads)

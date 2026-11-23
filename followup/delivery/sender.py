"""The sender interface, and the stand-ins for a real SMS provider.

In production `send` is the provider's API call and `lookup` is a query by our
reference, or the delivery webhook. The idempotency key is ours, one per draft.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class OutboundMessage:
    idempotency_key: str
    to_phone: str
    body: str
    channel: str = "sms"


@dataclass(frozen=True)
class SendResult:
    status: str                          # accepted, rejected, timeout
    provider_message_id: str | None = None
    error: str | None = None


def _provider_id(key: str) -> str:
    return "msg_" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


class Sender(Protocol):
    def send(self, message: OutboundMessage) -> SendResult: ...

    def lookup(self, idempotency_key: str) -> SendResult | None: ...


class OutboxSender:
    """Writes messages to memory, and to a JSON lines file if a path is given.

    Like a careful provider, it accepts each idempotency key once. A repeat returns
    the first result without delivering again.
    """

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else None
        self.delivered: dict[str, OutboundMessage] = {}

    def send(self, message: OutboundMessage) -> SendResult:
        if message.idempotency_key not in self.delivered:
            self.delivered[message.idempotency_key] = message
            if self.path:
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps({"key": message.idempotency_key, "to": message.to_phone,
                                         "channel": message.channel, "body": message.body}) + "\n")
        return SendResult("accepted", _provider_id(message.idempotency_key))

    def lookup(self, idempotency_key: str) -> SendResult | None:
        if idempotency_key in self.delivered:
            return SendResult("accepted", _provider_id(idempotency_key))
        return None


class ScriptedSender(OutboxSender):
    """An OutboxSender that can be told to reject, time out or crash on the next calls."""

    def __init__(self, script: list[str] | None = None):
        super().__init__()
        self.script = list(script or [])
        self.calls = 0

    def send(self, message: OutboundMessage) -> SendResult:
        self.calls += 1
        step = self.script.pop(0) if self.script else "accept"
        if step == "reject":
            return SendResult("rejected", error="provider rejected the message")
        if step == "timeout":
            return SendResult("timeout", error="no answer from provider")
        if step == "timeout_but_delivered":
            super().send(message)
            return SendResult("timeout", error="no answer from provider")
        if step == "crash":
            raise RuntimeError("process died before the provider call")
        return super().send(message)

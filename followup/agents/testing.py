"""Writers used by tests and by the page's guard view to stand in for a misbehaving model."""
from __future__ import annotations

from typing import Callable

from .base import WriterInput


class FixedWriter:
    """Returns whatever it is told to, or raises. A model that cannot be trusted."""

    def __init__(self, text: str | Callable[[WriterInput], str] | None = None, name: str = "fake-model",
                 error: Exception | None = None):
        self.name = name
        self._text = text
        self._error = error
        self.calls = 0
        self.seen: list[WriterInput] = []

    def write(self, inp: WriterInput) -> str:
        self.calls += 1
        self.seen.append(inp)
        if self._error is not None:
            raise self._error
        if callable(self._text):
            return self._text(inp)
        return self._text or ""

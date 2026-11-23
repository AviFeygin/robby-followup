"""Masks phone numbers before anything is logged."""
from __future__ import annotations

import logging
import re

_PHONE = re.compile(r"\+?\d[\d\-\s().]{7,}\d")


def mask_phone(text: str) -> str:
    def _mask(match: re.Match) -> str:
        digits = re.sub(r"\D", "", match.group(0))
        return "***" + digits[-2:]

    return _PHONE.sub(_mask, text)


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = mask_phone(record.getMessage())
        record.args = ()
        return True


def get_logger(name: str = "followup") -> logging.Logger:
    logger = logging.getLogger(name)
    if not any(isinstance(f, RedactingFilter) for f in logger.filters):
        logger.addFilter(RedactingFilter())
    return logger

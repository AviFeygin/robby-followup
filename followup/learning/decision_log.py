"""Decision log.

Each draft records the signals and baselines the policy saw, and the action it chose.
Outcomes are joined later. Without this a learning system has nothing to learn from,
and a past decision cannot be explained.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from .. import POLICY_VERSION
from ..platform.db import ShopSession
from ..platform.timeutil import iso


def record(session: ShopSession, draft_id: str, quote_id: str, now: datetime, signals: dict[str, Any],
           baselines: dict[str, Any], action: dict[str, Any]) -> None:
    session.run(
        "INSERT INTO decisions (shop_id, id, draft_id, quote_id, decided_at, signals_json, baselines_json, "
        "action_json, policy_version) VALUES (:shop_id, :id, :draft_id, :quote_id, :at, :signals, :baselines, "
        ":action, :pv)",
        id="L-" + uuid.uuid4().hex[:10], draft_id=draft_id, quote_id=quote_id, at=iso(now),
        signals=json.dumps(signals, sort_keys=True), baselines=json.dumps(baselines, sort_keys=True),
        action=json.dumps(action, sort_keys=True), pv=POLICY_VERSION,
    )


def read(session: ShopSession) -> list[dict[str, Any]]:
    return session.rows("SELECT * FROM decisions WHERE shop_id = :shop_id ORDER BY decided_at, id")

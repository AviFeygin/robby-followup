"""Shared fixtures. Every test passes a fixed "now" so nothing depends on the wall clock."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from followup.api import Engine  # noqa: E402
from followup.bootstrap import demo_engine, read_seed  # noqa: E402
from followup.platform.config import ShopConfig  # noqa: E402

NOW = "2026-08-17T14:00:00Z"     # 10:00 shop time, the morning after the last seed event
A, B = "shop-a", "shop-b"


@pytest.fixture(autouse=True)
def _fresh_database_per_test(monkeypatch):
    """Every test gets its own in-memory SQLite, even when DATABASE_URL is set.

    The Postgres tests pass their URL explicitly, so they are unaffected. Without this,
    setting DATABASE_URL would make every test share one Postgres database.
    """
    monkeypatch.delenv("DATABASE_URL", raising=False)


@pytest.fixture
def eng() -> Engine:
    return demo_engine()


@pytest.fixture
def seed():
    return read_seed()


def quote(qid: str, phone: str, amount: float, created: str, last_contact: str | None = None,
          status: str = "open", name: str = "Pat Example", tech: str = "Brad") -> dict:
    return {"id": qid, "customer_name": name, "customer_phone": phone, "tech_name": tech, "amount": amount,
            "status": status, "created_at": created, "last_contact_at": last_contact}


def event(eid: str, etype: str, qid: str, at: str, **extra) -> dict:
    return {"event_id": eid, "type": etype, "quote_id": qid, "timestamp": at, **extra}


def mini(quotes: list[dict], events: list[dict], config: ShopConfig | None = None, **kwargs) -> Engine:
    """A one shop engine for a hand built scenario."""
    engine = Engine(**kwargs)
    engine.create_shop("s1", "Test Shop", config or ShopConfig(shop_name="Test Shop"))
    engine.load_seed("s1", quotes, events)
    return engine


def by_quote(view: dict) -> dict:
    out = {c["quote_id"]: ("candidate", c) for c in view["candidates"]}
    out.update({e["quote_id"]: ("excluded", e) for e in view["excluded"]})
    return out


def run_and_approve(engine: Engine, shop: str = A, now: str = NOW) -> dict[str, str]:
    """Run the engine and approve every draft. Returns quote id to draft id."""
    created = engine.run(shop, now)["created"]
    for item in created:
        engine.approve(shop, item["draft_id"], "user:owner", "owner", now)
    return {item["quote_id"]: item["draft_id"] for item in created}

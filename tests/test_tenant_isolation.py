"""D. Tenant isolation and data leakage.

Two shops are loaded with the same quote ids, the same event ids and the same phone
numbers. That is the worst case for collisions. Shop B's customers are renamed with a
marker so any leak into shop A's output is easy to see.
"""
from __future__ import annotations

import json
import logging
import re

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from conftest import A, B, NOW, by_quote, event, quote, run_and_approve
from followup.agents.base import WriterInput
from followup.agents.prompt import ALLOWED_FIELDS, build_prompt
from followup.agents.testing import FixedWriter
from followup.api import PUBLIC, Engine, call
from followup.platform.config import demo_config
from followup.platform.db import ShopSession, TenantScopeError

MARK = "Zzyzx"
PHONE = re.compile(r"\+?1?917555\d{4}")


@pytest.fixture
def two(seed) -> Engine:
    quotes, events = seed
    engine = Engine()
    engine.create_shop(A, "Robby Home Services", demo_config())
    engine.create_shop(B, "Northside Repairs", demo_config().with_(shop_name="Northside Repairs"))
    engine.load_seed(A, quotes, events)
    renamed = [dict(q, customer_name=f"{MARK} {q['customer_name']}", amount=round(q["amount"] * 0.25)) for q in quotes]
    engine.load_seed(B, renamed + [quote("Q-B-ONLY", "+19175558888", 700, "2026-08-01T00:00:00Z", name=f"{MARK} Only")],
                     events)
    return engine


def test_no_shop_b_data_in_anything_shop_a_can_read(two):
    b_drafts = run_and_approve(two, B)
    a_drafts = run_and_approve(two, A)
    for d in list(a_drafts.values())[:5]:
        two.send(A, d, NOW)
    outputs = [two.overview(A, NOW), two.decisions(A), two.state_hash(A, NOW), two.messy_check(A, 3, 0.2, NOW)]
    outputs += [two.quote_detail(A, q["id"], NOW) for q in two.overview(A, NOW)["quotes"]]
    blob = json.dumps(outputs)
    assert MARK not in blob and "Q-B-ONLY" not in blob and "Northside" not in blob
    assert not set(b_drafts.values()) & set(re.findall(r"D-[0-9a-f]{10}", blob))


def test_the_same_event_id_is_stored_in_both_shops(two):
    assert two.overview(A, NOW)["counts"]["events"] == 82
    assert two.overview(B, NOW)["counts"]["events"] == 82


def test_same_phone_in_two_shops_is_two_customers_with_separate_cooldowns(two):
    for d in run_and_approve(two, A).values():
        two.send(A, d, NOW)
    later = "2026-08-17T15:00:00Z"
    assert by_quote(two.overview(A, later))["Q-1025"][1]["why"] == "cooldown"
    assert by_quote(two.overview(B, later))["Q-1025"][0] == "candidate"      # shop B is unaffected


def test_another_shops_draft_id_reads_as_not_found(two):
    b_draft = run_and_approve(two, B)["Q-1000"]
    for action in ("approve", "reject", "send", "reconcile"):
        foreign = call(two, action, {"shop_id": A, "draft_id": b_draft})
        missing = call(two, action, {"shop_id": A, "draft_id": "D-0000000000"})
        assert foreign == missing == {"ok": False, "error": {"code": "not_found", "message": "draft not found"}}
    assert call(two, "edit", {"shop_id": A, "draft_id": b_draft, "body": "x"})["error"]["code"] == "not_found"
    still = next(d for d in two.overview(B, NOW)["drafts"] if d["id"] == b_draft)
    assert still["status"] == "approved"
    assert two.sender.delivered == {}


def test_an_unknown_shop_reads_as_not_found(two):
    assert call(two, "overview", {"shop_id": "shop-zz"})["error"]["code"] == "not_found"


def test_the_database_rejects_a_draft_pointing_at_another_shops_quote(two):
    with pytest.raises(IntegrityError):
        with two.db.begin() as conn:
            conn.execute(text(
                "INSERT INTO drafts (shop_id, id, quote_id, customer_id, status, tier, reason_code, reason, strategy, "
                "body, written_by, created_at, policy_version) VALUES ('shop-a', 'D-x', 'Q-B-ONLY', 'C-19175558888', "
                "'draft', 1, 'x', 'x', 'nudge', 'x', 'template', '2026-08-17T14:00:00Z', 'v')"))


def test_a_shop_id_inside_a_payload_is_ignored(two):
    before = two.overview(B, NOW)["counts"]["events"]
    two.ingest(A, [dict(event("spoof-1", "quote_viewed", "Q-1000", "2026-08-17T10:00:00Z"), shop_id=B)])
    assert two.overview(B, NOW)["counts"]["events"] == before
    assert two.overview(A, NOW)["counts"]["events"] == 83
    raw = two.session(A).one("SELECT raw_json FROM events WHERE shop_id = :shop_id AND event_id = 'spoof-1'")
    assert "shop_id" not in json.loads(raw["raw_json"])


def test_every_table_is_keyed_by_shop_first(eng):
    inspector = inspect(eng.db)
    tables = inspector.get_table_names()
    assert len(tables) == 9
    for table in tables:
        pk = inspector.get_pk_constraint(table)["constrained_columns"]
        assert pk and pk[0] == "shop_id", f"{table} is not keyed by shop_id"
        for fk in inspector.get_foreign_keys(table):
            assert "shop_id" in fk["constrained_columns"], f"{table} has a foreign key that ignores the shop"


def test_a_statement_without_a_shop_filter_is_refused(eng):
    session = ShopSession(eng.db, A)
    with pytest.raises(TenantScopeError):
        session.rows("SELECT * FROM quotes")
    with pytest.raises(TenantScopeError):
        session.run("DELETE FROM drafts")
    with pytest.raises(TenantScopeError):
        ShopSession(eng.db, "")


def test_shop_a_baselines_do_not_move_when_shop_b_changes(two):
    before = two.overview(A, NOW)["baselines"]
    two.load_seed(B, [quote(f"Q-X{i}", f"+1917557{i:04d}", 250000, "2026-08-05T00:00:00Z") for i in range(40)], [])
    assert two.overview(A, NOW)["baselines"] == before
    assert two.overview(B, NOW)["baselines"] != before


def test_logs_from_a_full_run_contain_no_phone_number(two, caplog):
    with caplog.at_level(logging.DEBUG):
        for d in run_and_approve(two, A).values():
            two.send(A, d, NOW)
        two.ingest(A, [None, {"event_id": "bad", "type": "quote_viewed", "timestamp": "+19175552000"}])
    sending = [r.getMessage() for r in caplog.records if "sending draft" in r.getMessage()]
    assert sending and all("***" in line for line in sending)        # the number was there and was masked
    assert not PHONE.search(caplog.text)


def test_the_writer_sees_only_allow_listed_fields(two):
    assert set(WriterInput.__dataclass_fields__) == set(ALLOWED_FIELDS)
    spy = FixedWriter(lambda inp: "x")                                # rejected by the guard, template is used
    two.writer = spy
    two.run(A, NOW)
    assert spy.calls == len(two.overview(A, NOW)["drafts"]) > 0
    for inp in spy.seen:
        system, user = build_prompt(inp)
        prompt = system + user
        assert not PHONE.search(prompt) and "C-1917" not in prompt and "Q-10" not in prompt
        assert MARK not in prompt and "Northside" not in prompt
        # No surname and no other customer: the only customer word is the first name.
        full_names = {q["customer_name"] for q in two.overview(A, NOW)["quotes"]}
        assert not any(name in prompt for name in full_names)


def test_every_public_action_requires_a_known_shop_or_takes_none(two):
    no_shop = {"shops", "set_global_sending", "script_sender", "set_writer", "hostile_cases"}
    for name in sorted(PUBLIC - no_shop):
        result = call(two, name, {"shop_id": "nope"})
        assert result["ok"] is False, name

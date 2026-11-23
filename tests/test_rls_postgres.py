"""D, Postgres only. Row level security enforced by the database itself.

These run when DATABASE_URL points at a throwaway Postgres. They drop and recreate their
tables. With Postgres installed, a scratch cluster needs no Docker:

    initdb -D pgdata -U postgres -A trust
    pg_ctl -D pgdata -o "-p 5433" -w start

or with Docker:

    docker run -d --name followup-pg -e POSTGRES_PASSWORD=pg -p 5433:5432 postgres:16

then:

    pip install "psycopg[binary]"
    set DATABASE_URL=postgresql+psycopg://postgres:pg@localhost:5433/postgres
    pytest tests/test_rls_postgres.py

On SQLite they are skipped, because SQLite has no row level security.
"""
from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, text

from conftest import A, B, NOW
from followup.api import Engine
from followup.bootstrap import read_seed
from followup.platform.db import apply_rls, apply_schema

URL = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not URL.startswith("postgresql"), reason="needs DATABASE_URL for Postgres")

TABLES = ["shops", "customers", "quotes", "events", "drafts", "offers", "outbox", "baselines", "decisions"]


@pytest.fixture(scope="module")
def owner():
    engine = create_engine(URL)
    with engine.begin() as conn:
        for table in reversed(TABLES):
            conn.execute(text(f"DROP TABLE IF EXISTS {table} CASCADE"))
    apply_schema(engine)
    apply_rls(engine)
    return engine


@pytest.fixture(scope="module")
def app(owner):
    """The engine as the application runs it: as followup_app, which the policies bind."""
    engine = Engine(URL, db_role="followup_app", create_schema=False)
    quotes, events = read_seed()
    engine.load_demo(quotes, events)
    engine.run(A, NOW)
    engine.run(B, NOW)
    return engine


def _as_app(app, shop: str | None, sql: str):
    with app.db.begin() as conn:
        if shop is not None:
            conn.execute(text("SELECT set_config('app.shop_id', :s, true)"), {"s": shop})
        return conn.execute(text(sql)).fetchall()


def test_the_engine_works_end_to_end_under_row_level_security(app):
    assert app.overview(A, NOW)["counts"]["quotes"] == 30
    assert len(app.overview(A, NOW)["drafts"]) == 23


@pytest.mark.parametrize("table", TABLES)
def test_shop_a_sees_no_shop_b_rows_even_without_a_where_clause(app, table):
    rows = _as_app(app, A, f"SELECT DISTINCT shop_id FROM {table}")
    assert {r[0] for r in rows} <= {A}
    if table not in ("offers", "outbox"):
        assert rows, f"{table} should have shop A rows"


@pytest.mark.parametrize("table", TABLES)
def test_with_no_shop_set_every_table_reads_empty(app, table):
    assert _as_app(app, None, f"SELECT * FROM {table}") == []


def test_writing_a_row_for_another_shop_is_rejected(app):
    with pytest.raises(Exception) as err:
        _as_app(app, A, "INSERT INTO baselines (shop_id, metric, value, sample_n, source, computed_at) "
                        "VALUES ('shop-b', 'spoof', 1, 1, 'shop', 'now') RETURNING 1")
    assert "row-level security" in str(err.value)


def test_updating_another_shops_rows_changes_nothing(app):
    with app.db.begin() as conn:
        conn.execute(text("SELECT set_config('app.shop_id', :s, true)"), {"s": A})
        changed = conn.execute(text("UPDATE drafts SET body = 'tampered' WHERE shop_id = 'shop-b'")).rowcount
    assert changed == 0


def test_every_table_with_a_shop_id_has_forced_row_level_security(owner):
    """The test that keeps working as the system grows: a new table without a policy fails here."""
    with owner.begin() as conn:
        rows = conn.execute(text(
            "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity, "
            "(SELECT COUNT(*) FROM pg_policy p WHERE p.polrelid = c.oid) AS policies "
            "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relkind = 'r' AND EXISTS ("
            "  SELECT 1 FROM pg_attribute a WHERE a.attrelid = c.oid AND a.attname = 'shop_id' AND NOT a.attisdropped)"
        )).fetchall()
    assert {r[0] for r in rows} == set(TABLES)
    for name, enabled, forced, policies in rows:
        assert enabled and forced and policies >= 1, f"{name} is not protected"


def test_the_application_role_cannot_bypass_the_policies(owner):
    with owner.begin() as conn:
        role = conn.execute(text(
            "SELECT rolsuper, rolbypassrls, rolcreaterole, rolcreatedb FROM pg_roles WHERE rolname = 'followup_app'"
        )).one()
        owned = conn.execute(text(
            "SELECT COUNT(*) FROM pg_tables WHERE schemaname = 'public' AND tableowner = 'followup_app'"
        )).scalar()
    assert tuple(role) == (False, False, False, False)
    assert owned == 0

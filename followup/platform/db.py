"""Database access.

ShopSession is the only way the engine reaches the database. It is created for one
shop and refuses any statement that does not filter on :shop_id. On Postgres it also
sets the shop for the transaction so row level security can enforce the same thing
in the database.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "db" / "schema.sql"
RLS_PATH = Path(__file__).resolve().parents[2] / "db" / "rls_postgres.sql"


class TenantScopeError(RuntimeError):
    """Raised when a statement is not scoped to a shop."""


def make_engine(url: str | None = None, role: str | None = None) -> Engine:
    """Create an engine. Default is SQLite in memory. DATABASE_URL selects another."""
    url = url or os.environ.get("DATABASE_URL") or "sqlite://"
    if url.startswith("sqlite"):
        kwargs: dict[str, Any] = {"connect_args": {"check_same_thread": False}}
        if url in ("sqlite://", "sqlite:///:memory:"):
            kwargs["poolclass"] = StaticPool
        engine = create_engine(url, **kwargs)

        @event.listens_for(engine, "connect")
        def _on_connect(dbapi_conn, _record):  # pragma: no cover - wiring
            # Take over transaction control so BEGIN IMMEDIATE below is honoured.
            dbapi_conn.isolation_level = None
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA busy_timeout=5000")
            cur.close()

        @event.listens_for(engine, "begin")
        def _on_begin(conn):  # pragma: no cover - wiring
            # One writer at a time. The send claim relies on this on SQLite.
            conn.exec_driver_sql("BEGIN IMMEDIATE")

        return engine

    engine = create_engine(url)
    role = role or os.environ.get("FOLLOWUP_DB_ROLE")
    if role:
        if not role.replace("_", "").isalnum():
            raise ValueError("database role must be a plain identifier")

        @event.listens_for(engine, "connect")
        def _set_role(dbapi_conn, _record):  # pragma: no cover - Postgres only
            # Drop to the application role, which row level security applies to.
            cur = dbapi_conn.cursor()
            cur.execute(f'SET ROLE "{role}"')
            cur.close()
            dbapi_conn.commit()

    return engine


def apply_rls(engine: Engine) -> None:
    """Postgres only. Create the application role and the row level security policies."""
    # Sent through the raw driver cursor with no parameters. Through exec_driver_sql the
    # driver reads format()'s %I as a placeholder and the script never runs.
    with engine.begin() as conn:
        cursor = conn.connection.driver_connection.cursor()
        cursor.execute(RLS_PATH.read_text(encoding="utf-8"))
        cursor.close()


def apply_schema(engine: Engine, schema_sql: str | None = None) -> None:
    sql = schema_sql if schema_sql is not None else SCHEMA_PATH.read_text(encoding="utf-8")
    statements = [s.strip() for s in _strip_comments(sql).split(";") if s.strip()]
    with engine.begin() as conn:
        for stmt in statements:
            conn.execute(text(stmt))


def _strip_comments(sql: str) -> str:
    return "\n".join(line for line in sql.splitlines() if not line.strip().startswith("--"))


def is_postgres(engine: Engine) -> bool:
    return engine.dialect.name == "postgresql"


class ShopSession:
    """All reads and writes for one shop."""

    def __init__(self, engine: Engine, shop_id: str):
        if not shop_id or not isinstance(shop_id, str):
            raise TenantScopeError("a shop id is required")
        self.engine = engine
        self.shop_id = shop_id
        self._conn = None

    @contextmanager
    def tx(self) -> Iterator["ShopSession"]:
        """One transaction. Nested calls join the outer one."""
        if self._conn is not None:
            yield self
            return
        with self.engine.begin() as conn:
            self._conn = conn
            try:
                if is_postgres(self.engine):
                    conn.execute(text("SELECT set_config('app.shop_id', :s, true)"), {"s": self.shop_id})
                yield self
            finally:
                self._conn = None

    def _check(self, sql: str) -> None:
        if ":shop_id" not in sql:
            raise TenantScopeError("statement is not scoped to a shop: " + sql.strip()[:80])

    def rows(self, sql: str, **params: Any) -> list[dict[str, Any]]:
        self._check(sql)
        params["shop_id"] = self.shop_id
        with self.tx():
            result = self._conn.execute(text(sql), params)
            return [dict(r._mapping) for r in result]

    def one(self, sql: str, **params: Any) -> dict[str, Any] | None:
        found = self.rows(sql, **params)
        return found[0] if found else None

    def run(self, sql: str, **params: Any) -> int:
        """Execute a write. Returns the number of rows changed."""
        self._check(sql)
        params["shop_id"] = self.shop_id
        with self.tx():
            return self._conn.execute(text(sql), params).rowcount


def ensure_shop(engine: Engine, shop_id: str, org_id: str = "org-1", name: str | None = None) -> None:
    """Platform level setup, outside any shop session."""
    with engine.begin() as conn:
        if is_postgres(engine):
            conn.execute(text("SELECT set_config('app.shop_id', :s, true)"), {"s": shop_id})
        conn.execute(
            text(
                "INSERT INTO shops (shop_id, org_id, name) VALUES (:s, :o, :n) "
                "ON CONFLICT DO NOTHING"
            ),
            {"s": shop_id, "o": org_id, "n": name or shop_id},
        )

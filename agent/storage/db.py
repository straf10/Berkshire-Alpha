from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

import aiosqlite

from agent.storage import db_pg

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def _is_postgres(db_path: str) -> bool:
    return db_path.startswith("postgres://") or db_path.startswith("postgresql://")


@asynccontextmanager
async def connect(db_path: str) -> AsyncIterator[aiosqlite.Connection]:
    """Applies PRAGMA busy_timeout=5000 and PRAGMA foreign_keys=ON on EVERY connection
    (both are connection-scoped). journal_mode=WAL is database-scoped and set once,
    by init_db's schema.sql.

    Dispatches to db_pg when AGENT_DB_PATH is a postgres:// / postgresql:// DSN
    -- every caller uses storage_db.connect(settings.db_path) so this one
    branch is the entire backend switch. Production runs Postgres; SQLite
    remains the lightweight backend for local dev and tests."""
    if _is_postgres(db_path):
        async with db_pg.connect(db_path) as pg_conn:
            yield pg_conn  # type: ignore[misc]
        return

    # aiosqlite.Connection is a Thread subclass whose worker loop blocks on a
    # plain queue.get() until close() sends it a stop sentinel -- close()
    # skips that step entirely if _connection is ever None when it runs (see
    # aiosqlite/core.py), and that thread is non-daemon, so any code path
    # that abandons a connection without a clean close() hangs the WHOLE
    # interpreter at exit waiting to join a thread that will never stop.
    # Confirmed via faulthandler as the exact intermittent CI/local hang
    # (~30-50% of runs) in the test suite -- pre-existing, unrelated to any
    # of today's fixes. Marking it daemon must happen before connect()
    # starts the thread (aiosqlite.connect() returns the not-yet-started
    # Connection synchronously; awaiting it is what calls .start()), so a
    # leaked connection can never again block process shutdown. Production
    # runs Postgres (db_pg branch above) and is unaffected; SQLite is test/
    # local-dev only pending the planned full Postgres migration.
    raw_conn = aiosqlite.connect(db_path)
    raw_conn.daemon = True
    conn = await raw_conn
    conn.row_factory = aiosqlite.Row
    try:
        await conn.execute("PRAGMA busy_timeout=5000")
        await conn.execute("PRAGMA foreign_keys=ON")
        yield conn
    finally:
        await conn.close()


async def init_db(db_path: str) -> None:
    """Executes schema.sql, then _migrate(). Idempotent -- safe on every restart."""
    if _is_postgres(db_path):
        await db_pg.init_db(db_path)
        return

    schema = _SCHEMA_PATH.read_text(encoding="utf-8")
    async with connect(db_path) as conn:
        await conn.executescript(schema)
        await conn.commit()
        await _migrate(conn)
        await conn.commit()


async def _column_names(conn: aiosqlite.Connection, table: str) -> set[str]:
    cur = await conn.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in await cur.fetchall()}


async def _migrate(conn: aiosqlite.Connection) -> None:
    """Additive-only (docs/day3_llm_plan.md S1a, S1e). Day 2's agent.db predates
    both columns below and exists on the Railway volume; CREATE TABLE IF NOT
    EXISTS cannot add a column to a table that already exists, and SQLite has
    no ADD COLUMN IF NOT EXISTS."""
    if "max_loss_per_spread" not in await _column_names(conn, "trades"):
        await conn.execute("ALTER TABLE trades ADD COLUMN max_loss_per_spread REAL NOT NULL DEFAULT 0")
        # One-time backfill from the decision's persisted SpreadPlan. plan_json
        # serialises Decimal via default=str, so the extracted value is TEXT.
        await conn.execute("""
            UPDATE trades SET max_loss_per_spread = COALESCE((
              SELECT CAST(json_extract(d.plan_json, '$.max_loss_per_spread') AS REAL)
              FROM decisions d WHERE d.id = trades.decision_id
            ), 0) WHERE max_loss_per_spread = 0""")

    # See docs/day3_llm_plan.md S1e -- the velocity baseline must average raw
    # mention COUNTS, never the recursive `mention_velocity` column.
    if "mentions" not in await _column_names(conn, "sentiment_snapshots"):
        await conn.execute("ALTER TABLE sentiment_snapshots ADD COLUMN mentions INTEGER NOT NULL DEFAULT 0")

    # docs/day6_ui_plan.md S0.1. `_column_names` on a table that doesn't exist
    # yet (e.g. a pre-Day-3 DB, or a test DB seeded without debate_summaries)
    # returns an empty set with no error -- guard on non-empty so we never
    # ALTER a table that isn't there; schema.sql's CREATE TABLE IF NOT EXISTS
    # always creates it before _migrate() runs in real deployments.
    debate_summary_cols = await _column_names(conn, "debate_summaries")
    if debate_summary_cols and "conviction" not in debate_summary_cols:
        await conn.execute("ALTER TABLE debate_summaries ADD COLUMN conviction REAL")

    # P1-B (docs/phase1_premarket_execution.md S2.1): DEFAULT 0 is correct for
    # every pre-existing row -- none were CLI-verified. No backfill needed.
    if "cli_verified" not in await _column_names(conn, "trades"):
        await conn.execute("ALTER TABLE trades ADD COLUMN cli_verified INTEGER NOT NULL DEFAULT 0")

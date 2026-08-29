from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

import aiosqlite

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"


@asynccontextmanager
async def connect(db_path: str) -> AsyncIterator[aiosqlite.Connection]:
    """Applies PRAGMA busy_timeout=5000 and PRAGMA foreign_keys=ON on EVERY connection
    (both are connection-scoped). journal_mode=WAL is database-scoped and set once,
    by init_db's schema.sql."""
    conn = await aiosqlite.connect(db_path)
    conn.row_factory = aiosqlite.Row
    try:
        await conn.execute("PRAGMA busy_timeout=5000")
        await conn.execute("PRAGMA foreign_keys=ON")
        yield conn
    finally:
        await conn.close()


async def init_db(db_path: str) -> None:
    """Executes schema.sql, then _migrate(). Idempotent -- safe on every restart."""
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
    """Additive-only (docs/day3-llm-plan.md S1a, S1e). Day 2's agent.db predates
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

    # See docs/day3-llm-plan.md S1e -- the velocity baseline must average raw
    # mention COUNTS, never the recursive `mention_velocity` column.
    if "mentions" not in await _column_names(conn, "sentiment_snapshots"):
        await conn.execute("ALTER TABLE sentiment_snapshots ADD COLUMN mentions INTEGER NOT NULL DEFAULT 0")

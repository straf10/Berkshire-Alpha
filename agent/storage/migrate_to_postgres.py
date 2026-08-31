"""One-time data migration: copies every row from the live SQLite agent.db
into a Postgres database created by schema_pg.sql, preserving primary keys
so every foreign key (trades.decision_id, assignment_events.trade_id, and
every *.decision_id in the LLM-artifact tables) still resolves correctly.

Usage:
    python -m agent.storage.migrate_to_postgres <sqlite_path> <postgres_dsn>

Run against a COPY of agent.db, never the live file, while the agent is
still writing to it -- this script only reads from sqlite and only writes
to postgres. Kept around for one-off ops use; production has already been
cut over to Postgres.
"""
from __future__ import annotations

import asyncio
import sys

import aiosqlite
import asyncpg

# Insertion order matters: parents before the children that FK-reference them.
_TABLES_IN_ORDER = [
    "decisions",
    "trades",
    "assignment_events",
    "greeks_snapshots",
    "agent_state",
    "debates",
    "sentiment_snapshots",
    "llm_calls",
    "analyst_outputs",
    "debate_summaries",
    "proposals",
    "risk_votes",
    "tool_calls",
    "health_samples",
]

_HAS_SERIAL_ID = {t for t in _TABLES_IN_ORDER if t != "agent_state"}


async def _table_exists(sqlite_conn: aiosqlite.Connection, table: str) -> bool:
    cur = await sqlite_conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    )
    return await cur.fetchone() is not None


async def _migrate_table(sqlite_conn: aiosqlite.Connection, pg_conn: asyncpg.Connection, table: str) -> int:
    if not await _table_exists(sqlite_conn, table):
        return 0
    cur = await sqlite_conn.execute(f"SELECT * FROM {table}")
    rows = await cur.fetchall()
    if not rows:
        return 0

    columns = rows[0].keys()
    col_list = ", ".join(columns)
    placeholders = ", ".join(f"${i + 1}" for i in range(len(columns)))
    insert_sql = f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})"

    records = [tuple(row[c] for c in columns) for row in rows]
    await pg_conn.executemany(insert_sql, records)

    if table in _HAS_SERIAL_ID:
        await pg_conn.execute(
            f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
            f"(SELECT COALESCE(MAX(id), 1) FROM {table}))"
        )
    return len(records)


async def migrate(sqlite_path: str, postgres_dsn: str) -> dict[str, int]:
    from agent.storage import db_pg

    await db_pg.init_db(postgres_dsn)

    counts: dict[str, int] = {}
    sqlite_conn = await aiosqlite.connect(sqlite_path)
    sqlite_conn.row_factory = aiosqlite.Row
    pg_pool = await asyncpg.create_pool(postgres_dsn, min_size=1, max_size=1)
    try:
        async with pg_pool.acquire() as pg_conn:
            for table in _TABLES_IN_ORDER:
                counts[table] = await _migrate_table(sqlite_conn, pg_conn, table)
    finally:
        await sqlite_conn.close()
        await pg_pool.close()
    return counts


async def verify(sqlite_path: str, postgres_dsn: str) -> dict[str, tuple[int, int]]:
    """Returns {table: (sqlite_count, postgres_count)} -- caller asserts equal."""
    sqlite_conn = await aiosqlite.connect(sqlite_path)
    pg_pool = await asyncpg.create_pool(postgres_dsn, min_size=1, max_size=1)
    result: dict[str, tuple[int, int]] = {}
    try:
        async with pg_pool.acquire() as pg_conn:
            for table in _TABLES_IN_ORDER:
                if await _table_exists(sqlite_conn, table):
                    cur = await sqlite_conn.execute(f"SELECT COUNT(*) FROM {table}")
                    (sqlite_n,) = await cur.fetchone()
                else:
                    sqlite_n = 0
                pg_n = await pg_conn.fetchval(f"SELECT COUNT(*) FROM {table}")
                result[table] = (sqlite_n, pg_n)
    finally:
        await sqlite_conn.close()
        await pg_pool.close()
    return result


async def _main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit(f"usage: {sys.argv[0]} <sqlite_path> <postgres_dsn>")
    sqlite_path, postgres_dsn = sys.argv[1], sys.argv[2]

    counts = await migrate(sqlite_path, postgres_dsn)
    print("Rows migrated:")
    for table, n in counts.items():
        print(f"  {table}: {n}")

    print("\nVerifying row counts...")
    mismatches = []
    for table, (sqlite_n, pg_n) in (await verify(sqlite_path, postgres_dsn)).items():
        status = "OK" if sqlite_n == pg_n else "MISMATCH"
        if sqlite_n != pg_n:
            mismatches.append(table)
        print(f"  {table}: sqlite={sqlite_n} postgres={pg_n} [{status}]")

    if mismatches:
        raise SystemExit(f"\nMigration verification FAILED for: {mismatches}")
    print("\nAll row counts match.")


if __name__ == "__main__":
    asyncio.run(_main())

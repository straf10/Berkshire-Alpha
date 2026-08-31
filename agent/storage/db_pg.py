from __future__ import annotations

import asyncio
import re
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path
from typing import Any, AsyncIterator, Sequence

import asyncpg

_SCHEMA_PATH = Path(__file__).parent / "schema_pg.sql"

# Tables whose `id` column callers read back via cur.lastrowid after INSERT
# (agent_state's PK is `key`, not `id`, so it's deliberately excluded).
_HAS_ID = {
    "decisions", "trades", "greeks_snapshots", "assignment_events", "debates",
    "sentiment_snapshots", "llm_calls", "analyst_outputs", "debate_summaries",
    "proposals", "risk_votes", "tool_calls", "health_samples",
}
_INSERT_TABLE_RE = re.compile(r"(?is)^\s*INSERT\s+INTO\s+([a-zA-Z_][a-zA-Z0-9_]*)")

_pools: dict[str, asyncpg.Pool] = {}


async def _get_pool(dsn: str) -> asyncpg.Pool:
    pool = _pools.get(dsn)
    if pool is None:
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=10)
        _pools[dsn] = pool
    return pool


def _to_pg(sql: str) -> str:
    """`?` -> `$1, $2, ...` in appearance order -- every call site here uses
    purely positional `?` placeholders, so a left-to-right renumber is exact."""
    count = 0

    def repl(_: re.Match) -> str:
        nonlocal count
        count += 1
        return f"${count}"

    return re.sub(r"\?", repl, sql)


class _Row:
    """Wraps one asyncpg.Record, converting Decimal -> float on read (Postgres
    NUMERIC, e.g. AVG() over an integer column, where SQLite's equivalent
    query returns a plain float -- read.py is backend-agnostic and never
    expects a Decimal outside write.py's money boundary). Delegates
    everything else to the Record, which -- unlike a plain dict -- already
    supports both row[0] (main.py's positional fetchone() reads) and
    row["col"] (read.py's dict(row) conversions everywhere else); flattening
    to a dict here, as an earlier version of this function did, silently
    broke every row[0] call site."""

    __slots__ = ("_record",)

    def __init__(self, record: asyncpg.Record):
        self._record = record

    def __getitem__(self, key: int | str) -> Any:
        value = self._record[key]
        return float(value) if isinstance(value, Decimal) else value

    def keys(self) -> Any:
        return self._record.keys()

    def __iter__(self) -> Any:
        return iter(self._record)

    def __len__(self) -> int:
        return len(self._record)


class _PgCursor:
    def __init__(self, records: Sequence[Any] = (), lastrowid: int | None = None):
        self._records = list(records)
        self.lastrowid = lastrowid

    async def fetchall(self) -> list[Any]:
        return self._records

    async def fetchone(self) -> Any | None:
        return self._records[0] if self._records else None


class PgConnection:
    """Adapts an asyncpg connection to the tiny slice of aiosqlite.Connection's
    API that agent/storage/write.py and read.py use (execute/commit, and the
    cursor's fetchall/fetchone/lastrowid), so those two files run against
    Postgres completely unmodified.

    A raw asyncpg.Connection cannot run two operations concurrently (it
    raises InterfaceError: "another operation is in progress") -- unlike
    aiosqlite, which serialises statements on one connection, the exact
    guarantee every concurrent asyncio.gather() call site in this codebase
    (run_analysts' 3-per-candidate gather, in particular) was written
    against. `_lock` restores that guarantee here instead of auditing every
    gather() site, so this one scan's shared connection behaves the same
    under Postgres as it always has under sqlite."""

    def __init__(self, raw: asyncpg.Connection):
        self._raw = raw
        self._lock = asyncio.Lock()

    async def execute(self, sql: str, params: Sequence[Any] = ()) -> _PgCursor:
        async with self._lock:
            pg_sql = _to_pg(sql)
            m = _INSERT_TABLE_RE.match(sql)
            if m and m.group(1).lower() in _HAS_ID and "RETURNING" not in sql.upper():
                pg_sql = pg_sql.rstrip().rstrip(";") + " RETURNING id"
                row = await self._raw.fetchrow(pg_sql, *params)
                return _PgCursor(lastrowid=row["id"] if row is not None else None)

            stripped = sql.strip().upper()
            if stripped.startswith("SELECT") or stripped.startswith("WITH"):
                records = await self._raw.fetch(pg_sql, *params)
                return _PgCursor(records=[_Row(r) for r in records])

            await self._raw.execute(pg_sql, *params)
            return _PgCursor()

    async def executescript(self, script: str) -> None:
        await self._raw.execute(script)

    async def commit(self) -> None:
        """No-op: asyncpg auto-commits each statement outside an explicit
        transaction() block, which is how every call site here operates."""

    async def close(self) -> None:
        """No-op: connection lifetime is owned by the pool (see connect())."""


@asynccontextmanager
async def connect(dsn: str) -> AsyncIterator[PgConnection]:
    pool = await _get_pool(dsn)
    async with pool.acquire() as raw:
        yield PgConnection(raw)


async def init_db(dsn: str) -> None:
    """Idempotent: schema_pg.sql is all CREATE TABLE/INDEX IF NOT EXISTS, and
    unlike sqlite's schema.sql there is no additive _migrate() step -- this
    schema is created complete (see schema_pg.sql's header comment)."""
    pool = await _get_pool(dsn)
    schema = _SCHEMA_PATH.read_text(encoding="utf-8")
    async with pool.acquire() as raw:
        await raw.execute(schema)

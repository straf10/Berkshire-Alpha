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
    """Executes schema.sql. Idempotent -- safe on every restart."""
    schema = _SCHEMA_PATH.read_text(encoding="utf-8")
    async with connect(db_path) as conn:
        await conn.executescript(schema)
        await conn.commit()

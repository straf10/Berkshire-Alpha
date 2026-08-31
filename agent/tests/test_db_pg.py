from __future__ import annotations

import asyncio

from agent.storage.db_pg import PgConnection


class _FakeRecord(dict):
    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


class _ConcurrencyDetectingRaw:
    """Mirrors the one real property of asyncpg.Connection this bug depends
    on: it cannot run two operations at once. Raises exactly like asyncpg
    does (InterfaceError: "another operation is in progress") if a second
    call arrives while one is still in flight, instead of actually needing a
    live Postgres server to prove PgConnection serialises access to it."""

    def __init__(self) -> None:
        self._busy = False

    async def _guard(self):
        if self._busy:
            raise RuntimeError("cannot perform operation: another operation is in progress")
        self._busy = True
        await asyncio.sleep(0)  # yield, so a real race would interleave here
        try:
            yield
        finally:
            self._busy = False

    async def fetchrow(self, sql: str, *params):
        agen = self._guard()
        await agen.__anext__()
        try:
            return _FakeRecord(id=1)
        finally:
            await agen.aclose()

    async def fetch(self, sql: str, *params):
        agen = self._guard()
        await agen.__anext__()
        try:
            return []
        finally:
            await agen.aclose()

    async def execute(self, sql: str, *params):
        agen = self._guard()
        await agen.__anext__()
        try:
            return None
        finally:
            await agen.aclose()


async def test_concurrent_inserts_do_not_race() -> None:
    """Regression: run_analysts' asyncio.gather over QUANT/NEWS/SENTIMENT (and
    every other concurrent gather() site) shares ONE connection per scan
    cycle. Without PgConnection's lock, this raced against the fake driver's
    concurrency guard exactly like production did against real asyncpg
    (NEWS: InterfaceError). All ten must succeed with no exception."""
    conn = PgConnection(_ConcurrencyDetectingRaw())
    results = await asyncio.gather(
        *(conn.execute("INSERT INTO llm_calls (node) VALUES (?)", (f"NODE{i}",)) for i in range(10)),
        return_exceptions=True,
    )
    assert not any(isinstance(r, BaseException) for r in results), results


async def test_concurrent_selects_do_not_race() -> None:
    conn = PgConnection(_ConcurrencyDetectingRaw())
    results = await asyncio.gather(
        *(conn.execute("SELECT * FROM decisions WHERE id = ?", (i,)) for i in range(10)),
        return_exceptions=True,
    )
    assert not any(isinstance(r, BaseException) for r in results), results

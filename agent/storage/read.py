from __future__ import annotations

import json
from typing import Any

import aiosqlite

# imported ONLY by api/. No mutating SQL statements below this line.


async def latest_decisions(conn: aiosqlite.Connection, limit: int = 50) -> list[dict[str, Any]]:
    cur = await conn.execute("SELECT * FROM decisions ORDER BY ts_utc DESC LIMIT ?", (limit,))
    return [dict(row) for row in await cur.fetchall()]


async def latest_trades(conn: aiosqlite.Connection, limit: int = 50) -> list[dict[str, Any]]:
    cur = await conn.execute("SELECT * FROM trades ORDER BY ts_utc DESC LIMIT ?", (limit,))
    return [dict(row) for row in await cur.fetchall()]


async def latest_greeks(conn: aiosqlite.Connection) -> dict[str, Any] | None:
    cur = await conn.execute("SELECT * FROM greeks_snapshots ORDER BY ts_utc DESC LIMIT 1")
    row = await cur.fetchone()
    return dict(row) if row is not None else None


async def get_state(conn: aiosqlite.Connection, key: str) -> dict[str, Any] | None:
    cur = await conn.execute("SELECT * FROM agent_state WHERE key = ?", (key,))
    row = await cur.fetchone()
    if row is None:
        return None
    result = dict(row)
    result["value_json"] = json.loads(result["value_json"])
    return result


async def decision_chain(conn: aiosqlite.Connection, decision_id: int) -> dict[str, Any]:
    """decision + debates + trade"""
    cur = await conn.execute("SELECT * FROM decisions WHERE id = ?", (decision_id,))
    decision_row = await cur.fetchone()

    cur = await conn.execute("SELECT * FROM trades WHERE decision_id = ?", (decision_id,))
    trades = [dict(row) for row in await cur.fetchall()]

    cur = await conn.execute(
        "SELECT * FROM debates WHERE decision_id = ? ORDER BY round", (decision_id,)
    )
    debates = [dict(row) for row in await cur.fetchall()]

    return {
        "decision": dict(decision_row) if decision_row is not None else None,
        "trades": trades,
        "debates": debates,
    }

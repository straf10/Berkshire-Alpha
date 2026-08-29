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
    """decision + analyst_outputs + debates + debate_summary + proposal +
    risk_votes + trade + llm_calls -- the full reasoning chain in one request
    (docs/day3_llm_plan.md Group 5)."""
    cur = await conn.execute("SELECT * FROM decisions WHERE id = ?", (decision_id,))
    decision_row = await cur.fetchone()

    cur = await conn.execute("SELECT * FROM trades WHERE decision_id = ?", (decision_id,))
    trades = [dict(row) for row in await cur.fetchall()]

    cur = await conn.execute(
        "SELECT * FROM debates WHERE decision_id = ? ORDER BY round", (decision_id,)
    )
    debates = [dict(row) for row in await cur.fetchall()]

    cur = await conn.execute(
        "SELECT * FROM analyst_outputs WHERE decision_id = ? ORDER BY id", (decision_id,)
    )
    analyst_outputs = [dict(row) for row in await cur.fetchall()]

    cur = await conn.execute(
        "SELECT * FROM debate_summaries WHERE decision_id = ?", (decision_id,)
    )
    debate_summary_row = await cur.fetchone()

    cur = await conn.execute(
        "SELECT * FROM proposals WHERE decision_id = ?", (decision_id,)
    )
    proposal_row = await cur.fetchone()

    cur = await conn.execute(
        "SELECT * FROM risk_votes WHERE decision_id = ? ORDER BY id", (decision_id,)
    )
    risk_votes = [dict(row) for row in await cur.fetchall()]

    cur = await conn.execute(
        "SELECT * FROM llm_calls WHERE decision_id = ? ORDER BY id", (decision_id,)
    )
    llm_calls = [dict(row) for row in await cur.fetchall()]

    return {
        "decision": dict(decision_row) if decision_row is not None else None,
        "analyst_outputs": analyst_outputs,
        "debates": debates,
        "debate_summary": dict(debate_summary_row) if debate_summary_row is not None else None,
        "proposal": dict(proposal_row) if proposal_row is not None else None,
        "risk_votes": risk_votes,
        "trades": trades,
        "llm_calls": llm_calls,
    }

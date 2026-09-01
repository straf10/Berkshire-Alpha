from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
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


async def latest_assignments(conn: aiosqlite.Connection, limit: int = 50) -> list[dict[str, Any]]:
    cur = await conn.execute("SELECT * FROM assignment_events ORDER BY ts_utc DESC LIMIT ?", (limit,))
    return [dict(row) for row in await cur.fetchall()]


async def get_state(conn: aiosqlite.Connection, key: str) -> dict[str, Any] | None:
    cur = await conn.execute("SELECT * FROM agent_state WHERE key = ?", (key,))
    row = await cur.fetchone()
    if row is None:
        return None
    result = dict(row)
    result["value_json"] = json.loads(result["value_json"])
    return result


async def equity_history(conn: aiosqlite.Connection, limit: int = 500) -> list[dict[str, Any]]:
    """ts_utc/equity pairs, oldest first, from the greeks_snapshots time series
    written every 5-minute management tick plus at every scan (docs/day6_ui_plan.md
    S0.2) -- the dashboard's equity curve. Takes the most recent `limit` rows then
    reverses, rather than an unbounded ASC scan, so a long-running deployment can't
    make this query cost grow with total history."""
    cur = await conn.execute(
        "SELECT ts_utc, equity FROM greeks_snapshots ORDER BY ts_utc DESC LIMIT ?", (limit,)
    )
    rows = [dict(row) for row in await cur.fetchall()]
    rows.reverse()
    return rows


async def greeks_history(conn: aiosqlite.Connection, limit: int = 500) -> list[dict[str, Any]]:
    """Full greeks_snapshots rows, oldest first -- delta/vega trend, not just the
    single latest row `latest_greeks` returns (docs/day6_ui_plan.md S0.2)."""
    cur = await conn.execute(
        "SELECT * FROM greeks_snapshots ORDER BY ts_utc DESC LIMIT ?", (limit,)
    )
    rows = [dict(row) for row in await cur.fetchall()]
    rows.reverse()
    return rows


async def open_positions(conn: aiosqlite.Connection) -> list[dict[str, Any]]:
    """Open trades (closed_at IS NULL) with live per-leg greeks attached where
    available, joined in Python against the latest greeks_snapshots row's
    per_position_json (keyed on LegExposure.underlying == trades.symbol) rather
    than in SQL -- greeks_snapshots carries no trade_id, only the underlying
    symbol, and that symbol is not unique across concurrent positions in
    different underlyings, so a plain per-symbol match is exactly as precise
    as the two tables allow (docs/day6_ui_plan.md S0.2)."""
    cur = await conn.execute("SELECT * FROM trades WHERE closed_at IS NULL ORDER BY ts_utc DESC")
    trades = [dict(row) for row in await cur.fetchall()]

    legs_by_symbol: dict[str, list[dict[str, Any]]] = {}
    cur = await conn.execute("SELECT per_position_json FROM greeks_snapshots ORDER BY ts_utc DESC LIMIT 1")
    latest = await cur.fetchone()
    if latest is not None and latest["per_position_json"]:
        for leg in json.loads(latest["per_position_json"]):
            legs_by_symbol.setdefault(leg["underlying"], []).append(leg)

    for t in trades:
        t["live_legs"] = legs_by_symbol.get(t["symbol"], [])
    return trades


async def funnel(conn: aiosqlite.Connection, session_date: str | None = None) -> dict[str, Any]:
    """Screen -> shortlist -> debate -> gate breadth for one session, derived
    from `decisions` + `debate_summaries` (docs/day6_ui_plan.md S0.2 / S4). No new
    table: every stage is a count over rows already written by scan_cycle.

    Screened   = every decisions row this session (one per universe symbol that
                 got a row at all, including NO_TRADE ones excluded at the quant
                 screen).
    Shortlisted = rows whose regime.select() outcome was CREDIT or DEBIT AND made
                 the SHORTLIST_MAX cut (i.e. gate_reason is not one of
                 `regime.select`'s own NO_TRADE reasons -- NO_REGIME / DATA_NOT_OK
                 / DEBIT_NO_MOMENTUM_CONFIRMATION, see agent/strategy/regime.py --
                 and not NOT_SHORTLISTED, main.py's own truncation reason for a
                 regime-positive symbol that didn't make the top
                 SHORTLIST_MAX). Every other gate_reason (including
                 EARNINGS_BLACKOUT, a fund-manager gate rejection that implies
                 the symbol cleared the screen) means the symbol proceeded past
                 the deterministic screen.
    Debated    = rows with a debate_summaries entry.
    Entered    = action == 'ENTER'.
    """
    if session_date is None:
        cur = await conn.execute("SELECT session_date FROM decisions ORDER BY ts_utc DESC LIMIT 1")
        row = await cur.fetchone()
        session_date = row["session_date"] if row is not None else None

    if session_date is None:
        return {
            "session_date": None,
            "stages": [
                {"name": "screened", "count": 0, "top_reject_reason": None},
                {"name": "shortlisted", "count": 0, "top_reject_reason": None},
                {"name": "debated", "count": 0, "top_reject_reason": None},
                {"name": "entered", "count": 0, "top_reject_reason": None},
            ],
        }

    cur = await conn.execute(
        "SELECT id, mode, gate_reason, action FROM decisions WHERE session_date = ?", (session_date,)
    )
    rows = [dict(row) for row in await cur.fetchall()]

    cur = await conn.execute(
        "SELECT DISTINCT decision_id FROM debate_summaries WHERE decision_id IN "
        "(SELECT id FROM decisions WHERE session_date = ?)", (session_date,)
    )
    debated_ids = {r["decision_id"] for r in await cur.fetchall()}

    _SCREEN_STAGE_REJECTS = {
        "NO_REGIME", "DATA_NOT_OK", "DEBIT_NO_MOMENTUM_CONFIRMATION", "NOT_SHORTLISTED",
    }

    def _top_reason(candidates: list[dict[str, Any]]) -> str | None:
        if not candidates:
            return None
        counts: dict[str, int] = {}
        for r in candidates:
            counts[r["gate_reason"]] = counts.get(r["gate_reason"], 0) + 1
        return max(counts, key=lambda k: counts[k])

    screened = rows
    excluded_at_screen = [r for r in rows if r["gate_reason"] in _SCREEN_STAGE_REJECTS]
    shortlisted = [r for r in rows if r not in excluded_at_screen]
    debated = [r for r in shortlisted if r["id"] in debated_ids]
    not_debated = [r for r in shortlisted if r["id"] not in debated_ids]
    entered = [r for r in rows if r["action"] == "ENTER"]
    # Gate-stage reject reason, over every shortlisted row that didn't enter --
    # covers both debated candidates the gate rejected and quant-only candidates
    # that skipped debate entirely.
    not_entered = [r for r in shortlisted if r["action"] != "ENTER"]

    return {
        "session_date": session_date,
        "stages": [
            {"name": "screened", "count": len(screened), "top_reject_reason": _top_reason(excluded_at_screen)},
            {"name": "shortlisted", "count": len(shortlisted), "top_reject_reason": _top_reason(not_debated)},
            {"name": "debated", "count": len(debated), "top_reject_reason": _top_reason(not_entered)},
            {"name": "entered", "count": len(entered), "top_reject_reason": None},
        ],
    }


async def llm_usage(conn: aiosqlite.Connection, session_date: str | None = None) -> dict[str, Any]:
    """Tokens/cost/call-count per node (analyst/debate/trader/risk persona)
    and per model, from llm_calls -- every row already carries prompt_tokens,
    completion_tokens and est_cost_usd (agent/tools/llm.py), so this is a
    pure aggregation, no new instrumentation. session_date filters via
    decisions.session_date (llm_calls has no session_date of its own -- see
    write.py's comment on decision_id often being NULL at call time); a NULL
    decision_id row is excluded by the session filter, which only matters
    when session_date is passed."""
    where = ""
    params: tuple[Any, ...] = ()
    if session_date is not None:
        where = "WHERE decision_id IN (SELECT id FROM decisions WHERE session_date = ?)"
        params = (session_date,)

    cur = await conn.execute(
        f"""SELECT node, model,
               COUNT(*) AS calls,
               SUM(prompt_tokens) AS prompt_tokens,
               SUM(completion_tokens) AS completion_tokens,
               SUM(est_cost_usd) AS cost_usd
           FROM llm_calls {where}
           GROUP BY node, model
           ORDER BY cost_usd DESC""",
        params,
    )
    by_node_model = [dict(row) for row in await cur.fetchall()]

    cur = await conn.execute(
        f"""SELECT COUNT(*) AS calls,
               SUM(prompt_tokens) AS prompt_tokens,
               SUM(completion_tokens) AS completion_tokens,
               SUM(est_cost_usd) AS cost_usd
           FROM llm_calls {where}""",
        params,
    )
    totals = dict(await cur.fetchone())

    for row in [*by_node_model, totals]:
        row["prompt_tokens"] = row["prompt_tokens"] or 0
        row["completion_tokens"] = row["completion_tokens"] or 0
        row["cost_usd"] = row["cost_usd"] or 0.0

    return {"session_date": session_date, "totals": totals, "by_node_model": by_node_model}


async def tool_usage(conn: aiosqlite.Connection, session_date: str | None = None) -> dict[str, Any]:
    """Calls/latency/failures per (tool, endpoint) from tool_calls -- the
    non-LLM counterpart to llm_usage. No decision_id link (most of these
    calls happen outside any single decision's scope -- get_account,
    list_positions, management-tick reads), so session_date filters on
    ts_utc's YYYY-MM-DD date prefix instead of a decisions join."""
    where = ""
    params: tuple[Any, ...] = ()
    if session_date is not None:
        where = "WHERE ts_utc LIKE ?"
        params = (f"{session_date}%",)

    cur = await conn.execute(
        f"""SELECT tool, endpoint,
               COUNT(*) AS calls,
               SUM(CASE WHEN ok = 0 THEN 1 ELSE 0 END) AS failures,
               AVG(latency_ms) AS avg_latency_ms
           FROM tool_calls {where}
           GROUP BY tool, endpoint
           ORDER BY calls DESC""",
        params,
    )
    by_tool_endpoint = [dict(row) for row in await cur.fetchall()]

    cur = await conn.execute(
        f"""SELECT COUNT(*) AS calls,
               SUM(CASE WHEN ok = 0 THEN 1 ELSE 0 END) AS failures
           FROM tool_calls {where}""",
        params,
    )
    totals = dict(await cur.fetchone())
    totals["calls"] = totals["calls"] or 0
    totals["failures"] = totals["failures"] or 0

    for row in by_tool_endpoint:
        row["failures"] = row["failures"] or 0
        row["avg_latency_ms"] = row["avg_latency_ms"] or 0.0

    return {"session_date": session_date, "totals": totals, "by_tool_endpoint": by_tool_endpoint}


async def health_history(conn: aiosqlite.Connection, hours: int = 90) -> list[dict[str, Any]]:
    """Buckets health_samples into 1-hour windows over the last `hours` hours,
    oldest first -- the uptime strip's data source (status-page style: one
    bar per bucket, not one per raw sample). A bucket is 'down' if ANY
    sample inside it failed (a single bad management_tick is a real
    incident, not noise to average away into a percentage) and 'no_data' if
    the bucket has zero samples -- expected for market-closed hours, since
    management_tick (agent/main.py) only runs while the market's open;
    rendering that as 'down' would make the strip mostly red and meaningless.

    Buckets in Python rather than SQL to stay backend-agnostic -- SQLite's
    strftime and Postgres's date_trunc aren't the same dialect, and at most
    a few thousand rows for a 90-hour window is cheap to bucket in memory."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    cur = await conn.execute(
        "SELECT ts_utc, ok FROM health_samples WHERE ts_utc >= ? ORDER BY ts_utc ASC", (cutoff,)
    )
    rows = await cur.fetchall()

    now_hour = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    buckets: dict[datetime, list[int]] = {now_hour - timedelta(hours=i): [] for i in range(hours)}

    for row in rows:
        ts = datetime.fromisoformat(row["ts_utc"]).astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
        if ts in buckets:
            buckets[ts].append(row["ok"])

    result = []
    for bucket_start in sorted(buckets):
        samples = buckets[bucket_start]
        if not samples:
            status = "no_data"
        elif all(s == 1 for s in samples):
            status = "up"
        else:
            status = "down"
        result.append({
            "bucket_start_utc": bucket_start.isoformat(),
            "status": status,
            "ok_count": sum(samples),
            "total_count": len(samples),
        })
    return result


async def latest_reflections(conn: aiosqlite.Connection, limit: int = 30) -> list[dict[str, Any]]:
    """Day 4 (docs/day4_action_plan.md Step 5) -- the Reflector card's data
    source, newest session first."""
    cur = await conn.execute("SELECT * FROM reflections ORDER BY session_date DESC LIMIT ?", (limit,))
    return [dict(row) for row in await cur.fetchall()]


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

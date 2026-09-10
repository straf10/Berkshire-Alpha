from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Sequence

import aiosqlite

from agent.execution.order_manager import WalkResult

# imported only by main.py, execution/, risk/


@dataclass(frozen=True)
class DecisionRow:
    ts_utc: str
    cycle_id: str
    session_date: str
    symbol: str
    mode: str
    regime: str
    structure: str | None
    action: str
    gate_reason: str
    gate_detail: str
    observed_value: float | None
    threshold_value: float | None
    qty: int | None
    equity_feed: str
    earnings_armed: bool
    quant_json: str
    plan_json: str | None


@dataclass(frozen=True)
class TradeRow:
    decision_id: int
    ts_utc: str
    symbol: str
    structure: str
    expiry: str
    legs_json: str
    qty: int
    submitted_limit: Decimal
    order_id: str | None = None
    final_order_id: str | None = None
    final_limit: Decimal | None = None
    fill_price: Decimal | None = None
    filled_qty: int = 0
    walk_steps: int = 0
    status: str = "NEW"
    reject_code: str | None = None
    events_json: str = "[]"
    closed_at: str | None = None
    realized_pnl: Decimal | None = None
    # Day 3 (docs/day3_llm_plan.md S1a): the aggregate-defined-risk ledger.
    max_loss_per_spread: Decimal = Decimal("0")
    # P1-B: terminal state confirmed against the CLI, not just our own walk result.
    cli_verified: bool = False


@dataclass(frozen=True)
class DebateRow:
    decision_id: int
    ts_utc: str
    round: int
    persona: str
    doc_action: str
    evidence_cited_json: str
    volatility_view: str
    rebuttal_argument: str


@dataclass(frozen=True)
class SentimentSnapshotRow:
    ts_utc: str
    symbol: str
    source: str
    mention_velocity: float | None
    tone_score: float | None
    raw_json: str | None
    mentions: int = 0


@dataclass(frozen=True)
class LlmCallRow:
    ts_utc: str
    node: str
    provider: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int
    est_cost_usd: Decimal
    ok: bool
    decision_id: int | None = None
    retry_index: int = 0


@dataclass(frozen=True)
class AnalystOutputRow:
    decision_id: int
    ts_utc: str
    symbol: str
    analyst: str
    ok: bool
    output_json: str | None
    error: str | None


@dataclass(frozen=True)
class DebateSummaryRow:
    decision_id: int
    ts_utc: str
    rounds_run: int
    consensus_score: float
    verdict: str
    terminated_early: bool
    conviction: float | None = None


@dataclass(frozen=True)
class ProposalRow:
    decision_id: int
    ts_utc: str
    proposal_json: str
    accepted: bool
    reject_reason: str | None


@dataclass(frozen=True)
class RiskVoteRow:
    decision_id: int
    ts_utc: str
    persona: str
    decision: str
    max_loss_acceptable: bool
    risk_reward_ratio_acceptable: bool
    manager_notes: str


@dataclass(frozen=True)
class AssignmentEventRow:
    ts_utc: str
    session_date: str
    symbol: str
    trade_id: int | None
    reason: str
    assigned_right: str | None
    equity_qty: int
    contracts: int
    equity_status: str
    equity_order_id: str | None
    equity_fill_price: Decimal | None
    orphan_occ_symbol: str | None
    orphan_qty: int
    orphan_status: str
    orphan_order_id: str | None
    orphan_fill_price: Decimal | None
    detail: str


@dataclass(frozen=True)
class ToolCallRow:
    ts_utc: str
    tool: str
    endpoint: str
    ok: bool
    latency_ms: int
    error: str | None = None


@dataclass(frozen=True)
class HealthSampleRow:
    ts_utc: str
    ok: bool


@dataclass(frozen=True)
class ReflectionRow:
    ts_utc: str
    session_date: str
    decisions_examined: int
    binding_constraint: str
    constraint_count: int
    verdict: str
    argument: str
    proposed_change: str | None
    ok: bool
    # docs/fill_and_learning_plan.md P1-1: SELECTION | EXECUTION | EXIT, or
    # None when the LLM call failed (ok=False) or predates this field.
    stage: str | None = None


@dataclass(frozen=True)
class CounterfactualRow:
    """docs/fill_and_learning_plan.md P2. One re-quote of an unfilled entry's
    original legs -- see schema.sql's `counterfactuals` table comment.

    docs/strategy_audit_and_loop.md §5 B1/B3: `net_mid` and `settled` default
    so every existing call site keeps constructing unchanged. `net_mid` is
    the plan's entry-time net_mid (alongside `entry_at_natural`, so
    hypothetical_pnl's spread-cost/market-move split is derivable without
    plan_json). `settled` is True only for the one terminal row
    `_counterfactual_tick` writes once a contract has expired, valuing the
    spread at intrinsic from the settlement underlying price."""
    trade_id: int
    ts_utc: str
    would_have_filled: bool
    entry_at_natural: Decimal
    ev_at_entry: Decimal
    mark_to_market: Decimal
    hypothetical_pnl: Decimal
    detail: str
    net_mid: Decimal | None = None
    settled: bool = False


@dataclass(frozen=True)
class GreeksRow:
    ts_utc: str
    equity: Decimal
    delta_dollars: float
    vega_dollars: float
    delta_limit: float
    vega_limit: float
    breached: bool
    per_position_json: str


async def insert_decision(conn: aiosqlite.Connection, d: DecisionRow) -> int:
    cur = await conn.execute(
        """INSERT INTO decisions
           (ts_utc, cycle_id, session_date, symbol, mode, regime, structure, action,
            gate_reason, gate_detail, observed_value, threshold_value, qty,
            equity_feed, earnings_armed, quant_json, plan_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            d.ts_utc, d.cycle_id, d.session_date, d.symbol, d.mode, d.regime, d.structure,
            d.action, d.gate_reason, d.gate_detail, d.observed_value, d.threshold_value,
            d.qty, d.equity_feed, int(d.earnings_armed), d.quant_json, d.plan_json,
        ),
    )
    await conn.commit()
    assert cur.lastrowid is not None
    return cur.lastrowid


async def insert_trade(conn: aiosqlite.Connection, t: TradeRow) -> int:
    cur = await conn.execute(
        """INSERT INTO trades
           (decision_id, ts_utc, symbol, structure, expiry, legs_json, qty,
            submitted_limit, final_limit, fill_price, filled_qty, walk_steps,
            order_id, final_order_id, status, reject_code, events_json,
            closed_at, realized_pnl, max_loss_per_spread, cli_verified)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            t.decision_id, t.ts_utc, t.symbol, t.structure, t.expiry, t.legs_json, t.qty,
            float(t.submitted_limit),
            float(t.final_limit) if t.final_limit is not None else None,
            float(t.fill_price) if t.fill_price is not None else None,
            t.filled_qty, t.walk_steps, t.order_id, t.final_order_id, t.status,
            t.reject_code, t.events_json, t.closed_at,
            float(t.realized_pnl) if t.realized_pnl is not None else None,
            float(t.max_loss_per_spread), int(t.cli_verified),
        ),
    )
    await conn.commit()
    assert cur.lastrowid is not None
    return cur.lastrowid


async def update_trade_result(
    conn: aiosqlite.Connection, trade_id: int, r: WalkResult, *,
    max_loss_per_spread: Decimal | None = None,
) -> None:
    """`max_loss_per_spread`, when given, overwrites the mid-based value
    insert_trade wrote with the fill-derived truth (docs/audit_report_v2.md
    §6: plan.max_loss_per_spread is stale the instant the walk moves off
    mid). None (the default -- reject/no-fill paths) leaves the original
    build-time value in place, since there is no fill to derive a truer
    number from."""
    if max_loss_per_spread is not None:
        await conn.execute(
            """UPDATE trades SET status=?, final_order_id=?, final_limit=?, fill_price=?,
               filled_qty=?, walk_steps=?, reject_code=?, events_json=?, max_loss_per_spread=?,
               final_cap=? WHERE id=?""",
            (
                r.status, r.order_id,
                float(r.final_limit) if r.final_limit is not None else None,
                float(r.fill_price) if r.fill_price is not None else None,
                r.filled_qty, r.steps, r.reject_code,
                json.dumps([e.__dict__ for e in r.events], default=str),
                float(max_loss_per_spread),
                float(r.final_cap) if r.final_cap is not None else None,
                trade_id,
            ),
        )
    else:
        await conn.execute(
            """UPDATE trades SET status=?, final_order_id=?, final_limit=?, fill_price=?,
               filled_qty=?, walk_steps=?, reject_code=?, events_json=?, final_cap=? WHERE id=?""",
            (
                r.status, r.order_id,
                float(r.final_limit) if r.final_limit is not None else None,
                float(r.fill_price) if r.fill_price is not None else None,
                r.filled_qty, r.steps, r.reject_code,
                json.dumps([e.__dict__ for e in r.events], default=str),
                float(r.final_cap) if r.final_cap is not None else None,
                trade_id,
            ),
        )
    await conn.commit()


async def update_trade_order_id(
    conn: aiosqlite.Connection, trade_id: int, *, order_id: str, step: int
) -> None:
    """Called on SUBMIT (step 0) and after EVERY replace_order. `order_id` is
    written once at step 0 and never again -- it is the anchor of the replace
    chain. `final_order_id` is overwritten on every step, because
    replace_order mints a NEW id (order_manager.py:150) and only the newest id
    is live at the broker. Commits per call: the whole point is that the row
    survives a kill -9 between two steps."""
    await conn.execute(
        """UPDATE trades SET order_id = COALESCE(order_id, ?), final_order_id = ?,
           walk_steps = ?, status = ? WHERE id = ?""",
        (order_id, order_id, step, "ACCEPTED", trade_id),
    )
    await conn.commit()


@dataclass(frozen=True)
class TradeRepair:
    status: str
    final_order_id: str | None
    final_limit: Decimal | None
    fill_price: Decimal | None
    filled_qty: int
    walk_steps: int
    reject_code: str | None
    cli_verified: bool


async def repair_trade(conn: aiosqlite.Connection, trade_id: int, r: TradeRepair) -> None:
    """The ONLY writer used by startup_reconcile. Deliberately touches neither
    `closed_at` nor `realized_pnl` -- an open entry position has no realized
    P&L, and `close_trade` remains their sole writer (see §3.2)."""
    await conn.execute(
        """UPDATE trades SET status=?, final_order_id=?, final_limit=?, fill_price=?,
           filled_qty=?, walk_steps=?, reject_code=?, cli_verified=? WHERE id=?""",
        (
            r.status, r.final_order_id,
            float(r.final_limit) if r.final_limit is not None else None,
            float(r.fill_price) if r.fill_price is not None else None,
            r.filled_qty, r.walk_steps, r.reject_code, int(r.cli_verified),
            trade_id,
        ),
    )
    await conn.commit()


async def insert_tool_call(conn: aiosqlite.Connection, t: ToolCallRow) -> int:
    cur = await conn.execute(
        """INSERT INTO tool_calls (ts_utc, tool, endpoint, ok, latency_ms, error)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (t.ts_utc, t.tool, t.endpoint, int(t.ok), t.latency_ms, t.error),
    )
    await conn.commit()
    assert cur.lastrowid is not None
    return cur.lastrowid


async def insert_health_sample(conn: aiosqlite.Connection, h: HealthSampleRow) -> int:
    cur = await conn.execute(
        "INSERT INTO health_samples (ts_utc, ok) VALUES (?, ?)",
        (h.ts_utc, int(h.ok)),
    )
    await conn.commit()
    assert cur.lastrowid is not None
    return cur.lastrowid


async def insert_reflection(conn: aiosqlite.Connection, r: ReflectionRow) -> int:
    """INSERT ... ON CONFLICT(session_date) DO NOTHING -- the UNIQUE
    constraint is the idempotency guarantee, this is the application-level
    optimisation in front of it. Deliberately re-SELECTs the id afterward
    rather than trusting `cur.lastrowid`/`cur.rowcount`: those have different
    semantics between aiosqlite's raw cursor and db_pg.py's asyncpg adapter
    (PgConnection.execute only attaches a RETURNING clause for tables in
    `_HAS_ID`, which this table deliberately isn't), so a plain follow-up
    SELECT is the one thing guaranteed to behave identically on both
    backends."""
    await conn.execute(
        """INSERT INTO reflections
           (ts_utc, session_date, decisions_examined, binding_constraint, constraint_count,
            verdict, argument, proposed_change, ok, stage)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(session_date) DO NOTHING""",
        (
            r.ts_utc, r.session_date, r.decisions_examined, r.binding_constraint,
            r.constraint_count, r.verdict, r.argument, r.proposed_change, int(r.ok), r.stage,
        ),
    )
    await conn.commit()
    cur = await conn.execute("SELECT id FROM reflections WHERE session_date = ?", (r.session_date,))
    row = await cur.fetchone()
    assert row is not None
    return int(row[0])


async def insert_counterfactual(conn: aiosqlite.Connection, r: CounterfactualRow) -> None:
    await conn.execute(
        """INSERT INTO counterfactuals
           (trade_id, ts_utc, would_have_filled, entry_at_natural, net_mid, ev_at_entry,
            mark_to_market, hypothetical_pnl, settled, detail)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            r.trade_id, r.ts_utc, int(r.would_have_filled), float(r.entry_at_natural),
            float(r.net_mid) if r.net_mid is not None else None,
            float(r.ev_at_entry), float(r.mark_to_market), float(r.hypothetical_pnl),
            int(r.settled), r.detail,
        ),
    )
    await conn.commit()


async def insert_greeks_snapshot(conn: aiosqlite.Connection, g: GreeksRow) -> int:
    cur = await conn.execute(
        """INSERT INTO greeks_snapshots
           (ts_utc, equity, delta_dollars, vega_dollars, delta_limit, vega_limit,
            breached, per_position_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            g.ts_utc, float(g.equity), g.delta_dollars, g.vega_dollars,
            g.delta_limit, g.vega_limit, int(g.breached), g.per_position_json,
        ),
    )
    await conn.commit()
    assert cur.lastrowid is not None
    return cur.lastrowid


async def insert_assignment_event(conn: aiosqlite.Connection, a: AssignmentEventRow) -> int:
    """Assignment Reconciliation Routine (docs/assignment_reconciliation_plan.md
    Group 3) -- audit/dashboard only, never consulted before submitting an
    order (§0.5 layer 3 is explicitly not a gate). Deliberately NOT a
    decisions row -- see main.py's _completed_scan_count (§A3)."""
    cur = await conn.execute(
        """INSERT INTO assignment_events
           (ts_utc, session_date, symbol, trade_id, reason, assigned_right, equity_qty,
            contracts, equity_status, equity_order_id, equity_fill_price, orphan_occ_symbol,
            orphan_qty, orphan_status, orphan_order_id, orphan_fill_price, detail)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            a.ts_utc, a.session_date, a.symbol, a.trade_id, a.reason, a.assigned_right,
            a.equity_qty, a.contracts, a.equity_status, a.equity_order_id,
            float(a.equity_fill_price) if a.equity_fill_price is not None else None,
            a.orphan_occ_symbol, a.orphan_qty, a.orphan_status, a.orphan_order_id,
            float(a.orphan_fill_price) if a.orphan_fill_price is not None else None,
            a.detail,
        ),
    )
    await conn.commit()
    assert cur.lastrowid is not None
    return cur.lastrowid


async def put_state(conn: aiosqlite.Connection, key: str, value: Any) -> None:
    """agent_state upsert."""
    ts = datetime.now(timezone.utc).isoformat()
    await conn.execute(
        """INSERT INTO agent_state (key, ts_utc, value_json) VALUES (?, ?, ?)
           ON CONFLICT(key) DO UPDATE SET ts_utc=excluded.ts_utc, value_json=excluded.value_json""",
        (key, ts, json.dumps(value, default=str)),
    )
    await conn.commit()


# --------------------------------------------------------------------------
# Day 3 (docs/day3_llm_plan.md S1c/S1e): the LLM artifact tables. analyst_outputs
# / debate_summaries / proposals / risk_votes / debates all carry a NOT NULL
# decision_id FK -- callers must insert AFTER insert_decision (PipelineArtifacts,
# Group 5). llm_calls is the deliberate exception: written at call time with
# decision_id=NULL so budget accounting is right even for dropped candidates.
# --------------------------------------------------------------------------


async def insert_debate(conn: aiosqlite.Connection, d: DebateRow) -> int:
    cur = await conn.execute(
        """INSERT INTO debates
           (decision_id, ts_utc, round, persona, doc_action, evidence_cited_json,
            volatility_view, rebuttal_argument)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            d.decision_id, d.ts_utc, d.round, d.persona, d.doc_action,
            d.evidence_cited_json, d.volatility_view, d.rebuttal_argument,
        ),
    )
    await conn.commit()
    assert cur.lastrowid is not None
    return cur.lastrowid


async def insert_sentiment_snapshot(conn: aiosqlite.Connection, s: SentimentSnapshotRow) -> int:
    cur = await conn.execute(
        """INSERT INTO sentiment_snapshots
           (ts_utc, symbol, source, mention_velocity, tone_score, raw_json, mentions)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (s.ts_utc, s.symbol, s.source, s.mention_velocity, s.tone_score, s.raw_json, s.mentions),
    )
    await conn.commit()
    assert cur.lastrowid is not None
    return cur.lastrowid


async def insert_llm_call(conn: aiosqlite.Connection, c: LlmCallRow) -> int:
    """Written at call time -- decision_id is usually None (back-linked later
    via update_llm_calls_decision_id, once a decisions row exists)."""
    cur = await conn.execute(
        """INSERT INTO llm_calls
           (ts_utc, decision_id, node, provider, model, prompt_tokens, completion_tokens,
            latency_ms, est_cost_usd, retry_index, ok)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            c.ts_utc, c.decision_id, c.node, c.provider, c.model, c.prompt_tokens,
            c.completion_tokens, c.latency_ms, float(c.est_cost_usd), c.retry_index, int(c.ok),
        ),
    )
    await conn.commit()
    assert cur.lastrowid is not None
    return cur.lastrowid


async def update_llm_calls_decision_id(conn: aiosqlite.Connection, call_ids: Sequence[int], decision_id: int) -> None:
    if not call_ids:
        return
    placeholders = ",".join("?" * len(call_ids))
    await conn.execute(
        f"UPDATE llm_calls SET decision_id = ? WHERE id IN ({placeholders})",
        (decision_id, *call_ids),
    )
    await conn.commit()


async def insert_analyst_output(conn: aiosqlite.Connection, a: AnalystOutputRow) -> int:
    cur = await conn.execute(
        """INSERT INTO analyst_outputs (decision_id, ts_utc, symbol, analyst, ok, output_json, error)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (a.decision_id, a.ts_utc, a.symbol, a.analyst, int(a.ok), a.output_json, a.error),
    )
    await conn.commit()
    assert cur.lastrowid is not None
    return cur.lastrowid


async def insert_debate_summary(conn: aiosqlite.Connection, s: DebateSummaryRow) -> int:
    cur = await conn.execute(
        """INSERT INTO debate_summaries
           (decision_id, ts_utc, rounds_run, consensus_score, verdict, terminated_early, conviction)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (s.decision_id, s.ts_utc, s.rounds_run, s.consensus_score, s.verdict, int(s.terminated_early), s.conviction),
    )
    await conn.commit()
    assert cur.lastrowid is not None
    return cur.lastrowid


async def insert_proposal(conn: aiosqlite.Connection, p: ProposalRow) -> int:
    cur = await conn.execute(
        """INSERT INTO proposals (decision_id, ts_utc, proposal_json, accepted, reject_reason)
           VALUES (?, ?, ?, ?, ?)""",
        (p.decision_id, p.ts_utc, p.proposal_json, int(p.accepted), p.reject_reason),
    )
    await conn.commit()
    assert cur.lastrowid is not None
    return cur.lastrowid


async def close_trade(
    conn: aiosqlite.Connection, trade_id: int, *, closed_at: str, realized_pnl: Decimal,
    exit_reason: str | None = None,
) -> None:
    """Day 4 exits (docs/day3_llm_plan.md's own §0.1 blocking-gap note): the
    ONLY writer of `closed_at`. Until this lands, `_open_defined_risk`'s
    ledger only ever grows within a session -- see main.py's exit_tick.

    `exit_reason` (P2 remediation, docs/audit_report_v2.md §9 item 10) is the
    ExitReason (agent/risk/exits.py) that triggered this close, or None for a
    non-`evaluate_exit` close path (e.g. assignment resolution) that has no
    such reason to report."""
    await conn.execute(
        "UPDATE trades SET closed_at = ?, realized_pnl = ?, exit_reason = ? WHERE id = ?",
        (closed_at, float(realized_pnl), exit_reason, trade_id),
    )
    await conn.commit()


async def insert_risk_vote(conn: aiosqlite.Connection, v: RiskVoteRow) -> int:
    cur = await conn.execute(
        """INSERT INTO risk_votes
           (decision_id, ts_utc, persona, decision, max_loss_acceptable,
            risk_reward_ratio_acceptable, manager_notes)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            v.decision_id, v.ts_utc, v.persona, v.decision, int(v.max_loss_acceptable),
            int(v.risk_reward_ratio_acceptable), v.manager_notes,
        ),
    )
    await conn.commit()
    assert cur.lastrowid is not None
    return cur.lastrowid


@dataclass(frozen=True)
class ChainSnapshotRow:
    """docs/prompts/real_iv_surface_free.md Path B -- one row per contract in
    a real chain ChainCache.load already fetched (feed=indicative, strikes/
    bids/asks/greeks/IV) and, before this table existed, threw away after one
    decision cycle. Never read by any live decision path -- research data
    only (schema.sql's/schema_pg.sql's chain_snapshots comment)."""
    cycle_id: str
    ts_utc: str
    session_date: str
    underlying: str
    occ_symbol: str
    expiry: str
    strike: float
    right: str
    bid: float
    ask: float
    delta: float
    gamma: float
    theta: float
    vega: float
    iv: float


# Rows per multi-row INSERT: 15 columns/row, so 300 rows is 4,500 bound
# parameters -- comfortably under Postgres's ~65,535 protocol limit (asyncpg's
# `_to_pg` renumbers every `?` to `$1.."$N` for one statement) with a wide
# margin for a busier-than-expected cycle, while still being a small number of
# statements rather than one per contract.
_CHAIN_SNAPSHOT_BATCH = 300


async def insert_chain_snapshots(conn: aiosqlite.Connection, rows: Sequence[ChainSnapshotRow]) -> None:
    """Batched insert for one scan cycle's real chain(s) -- every symbol's
    contracts in a handful of multi-row statements, never one INSERT per
    contract. Caller (main.py's scan_cycle, right after chain_cache.load())
    wraps this in try/except: a write failure here must never propagate and
    block a trade, since this table is research data, not a decision input."""
    if not rows:
        return
    for i in range(0, len(rows), _CHAIN_SNAPSHOT_BATCH):
        batch = rows[i : i + _CHAIN_SNAPSHOT_BATCH]
        placeholders = ", ".join(["(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"] * len(batch))
        params: list[Any] = []
        for r in batch:
            params.extend((
                r.cycle_id, r.ts_utc, r.session_date, r.underlying, r.occ_symbol, r.expiry,
                r.strike, r.right, r.bid, r.ask, r.delta, r.gamma, r.theta, r.vega, r.iv,
            ))
        await conn.execute(
            f"""INSERT INTO chain_snapshots
               (cycle_id, ts_utc, session_date, underlying, occ_symbol, expiry,
                strike, right, bid, ask, delta, gamma, theta, vega, iv)
               VALUES {placeholders}""",
            params,
        )
    await conn.commit()

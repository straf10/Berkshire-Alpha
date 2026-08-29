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
            closed_at, realized_pnl, max_loss_per_spread)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            t.decision_id, t.ts_utc, t.symbol, t.structure, t.expiry, t.legs_json, t.qty,
            float(t.submitted_limit),
            float(t.final_limit) if t.final_limit is not None else None,
            float(t.fill_price) if t.fill_price is not None else None,
            t.filled_qty, t.walk_steps, t.order_id, t.final_order_id, t.status,
            t.reject_code, t.events_json, t.closed_at,
            float(t.realized_pnl) if t.realized_pnl is not None else None,
            float(t.max_loss_per_spread),
        ),
    )
    await conn.commit()
    assert cur.lastrowid is not None
    return cur.lastrowid


async def update_trade_result(conn: aiosqlite.Connection, trade_id: int, r: WalkResult) -> None:
    await conn.execute(
        """UPDATE trades SET status=?, final_order_id=?, final_limit=?, fill_price=?,
           filled_qty=?, walk_steps=?, reject_code=?, events_json=? WHERE id=?""",
        (
            r.status, r.order_id,
            float(r.final_limit) if r.final_limit is not None else None,
            float(r.fill_price) if r.fill_price is not None else None,
            r.filled_qty, r.steps, r.reject_code,
            json.dumps([e.__dict__ for e in r.events], default=str),
            trade_id,
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
           (decision_id, ts_utc, rounds_run, consensus_score, verdict, terminated_early)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (s.decision_id, s.ts_utc, s.rounds_run, s.consensus_score, s.verdict, int(s.terminated_early)),
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

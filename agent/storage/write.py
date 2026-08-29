from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import aiosqlite

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
            closed_at, realized_pnl)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            t.decision_id, t.ts_utc, t.symbol, t.structure, t.expiry, t.legs_json, t.qty,
            float(t.submitted_limit),
            float(t.final_limit) if t.final_limit is not None else None,
            float(t.fill_price) if t.fill_price is not None else None,
            t.filled_qty, t.walk_steps, t.order_id, t.final_order_id, t.status,
            t.reject_code, t.events_json, t.closed_at,
            float(t.realized_pnl) if t.realized_pnl is not None else None,
        ),
    )
    await conn.commit()
    assert cur.lastrowid is not None
    return cur.lastrowid


async def update_trade_result(conn: aiosqlite.Connection, trade_id: int, r: Any) -> None:
    """`r` is Group 5's WalkResult -- duck-typed here since execution/order_manager.py
    (status, order_id, final_limit, fill_price, filled_qty, steps, reject_code, events)
    lands in Group 5, after this module."""
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

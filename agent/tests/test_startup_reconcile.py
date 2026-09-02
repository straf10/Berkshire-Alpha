"""P1-B (docs/phase1_premarket_execution.md S2.4/S2.7): startup_reconcile and
the order-id-sink wiring that anchors it. Reuses test_main.py's FakeClients /
_deps / _patch_cli fixtures rather than re-deriving them."""

from __future__ import annotations

import dataclasses
import json
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from agent import main as main_module
from agent.execution import cli_bridge
from agent.execution.broker import MockBroker
from agent.schemas.execution import Intent, Leg, Regime, SpreadPlan, Structure
from agent.storage import db as storage_db
from agent.storage import write as storage_write
from agent.tests.test_main import FakeClients, SESSION_DATE, _deps, _FastClock, _immediate, _patch_cli

NOW = datetime(2026, 8, 31, 17, 0, tzinfo=timezone.utc)


def _plan(max_loss: str = "210") -> SpreadPlan:
    legs = (
        Leg(occ_symbol="TST260904P00100000", strike=100.0, right="P", side="SELL", ratio_qty=1,
            intent=Intent.SELL_TO_OPEN, delta=-0.275, vega=0.05, bid=1.0, ask=1.1),
        Leg(occ_symbol="TST260904P00097000", strike=97.0, right="P", side="BUY", ratio_qty=1,
            intent=Intent.BUY_TO_OPEN, delta=-0.10, vega=0.03, bid=0.2, ask=0.3),
    )
    return SpreadPlan(
        symbol="TST", structure=Structure.BULL_PUT_SPREAD, regime=Regime.CREDIT,
        expiry=date(2026, 9, 4), dte=4, legs=legs, width=3.0,
        net_mid=Decimal("-0.90"), net_natural=Decimal("-0.75"),
        max_profit_per_spread=Decimal("90"), max_loss_per_spread=Decimal(max_loss),
        p_success=0.72, spot=100.0, short_leg_delta=0.275,
    )


async def _seed(
    conn, *, status: str, order_id: str | None, final_order_id: str | None,
    qty: int = 2, filled_qty: int = 0, plan: SpreadPlan | None = None,
) -> int:
    plan = plan or _plan()
    decision_id = await storage_write.insert_decision(conn, storage_write.DecisionRow(
        ts_utc="t", cycle_id="seed", session_date=SESSION_DATE.isoformat(), symbol=plan.symbol,
        mode="quant-only", regime=plan.regime.value, structure=plan.structure.value, action="ENTER",
        gate_reason="APPROVED", gate_detail="APPROVED", observed_value=None, threshold_value=None,
        qty=qty, equity_feed="iex", earnings_armed=False, quant_json="{}",
        plan_json=json.dumps(dataclasses.asdict(plan), default=str),
    ))
    trade = storage_write.TradeRow(
        decision_id=decision_id, ts_utc="t", symbol=plan.symbol, structure=plan.structure.value,
        expiry=plan.expiry.isoformat(),
        legs_json=json.dumps([dataclasses.asdict(leg) for leg in plan.legs], default=str),
        qty=qty, submitted_limit=plan.net_mid, order_id=order_id, final_order_id=final_order_id,
        filled_qty=filled_qty, status=status, max_loss_per_spread=plan.max_loss_per_spread,
    )
    return await storage_write.insert_trade(conn, trade)


def _reconcile_deps(db_path: str, *, broker: MockBroker | None = None) -> main_module.Deps:
    return _deps(db_path, FakeClients(), broker or MockBroker([]), _FastClock(NOW))


def _fake_run_no_positions(order_responder):
    """Wraps an order-get responder so `_is_position_class`'s own
    `cli_bridge.list_positions()` call (`_run(["position", "list"])`) gets a
    well-shaped empty list instead of whatever the order-get branch would
    have returned for mismatched args."""

    async def fake_run(args, *, timeout: float = 10.0):
        if args[:2] == ["position", "list"]:
            return []
        return await order_responder(args, timeout=timeout)

    return fake_run


async def test_reconcile_skips_terminal_rows(tmp_path) -> None:
    db_path = str(tmp_path / "agent.db")
    await storage_db.init_db(db_path)
    async with storage_db.connect(db_path) as conn:
        await _seed(conn, status="FILLED", order_id="o1", final_order_id="o1", filled_qty=2)
        report = await main_module.startup_reconcile(_reconcile_deps(db_path), conn)

    assert report == main_module.ReconcileReport(
        inspected=0, repaired=0, unresolved_transient=0, unresolved_position=0, cancelled_working=0
    )


async def test_reconcile_follows_replace_chain_to_head(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = str(tmp_path / "agent.db")
    await storage_db.init_db(db_path)
    async with storage_db.connect(db_path) as conn:
        trade_id = await _seed(conn, status="ACCEPTED", order_id="o1", final_order_id="o1", qty=2)

    async def fake_run(args, *, timeout: float = 10.0):
        oid = args[3]
        if oid == "o1":
            return {"id": "o1", "status": "replaced", "replaced_by": "o1-r1", "filled_qty": "0",
                     "filled_avg_price": None, "limit_price": "-0.90"}
        if oid == "o1-r1":
            return {"id": "o1-r1", "status": "filled", "replaced_by": None, "filled_qty": "2",
                     "filled_avg_price": "-0.90", "limit_price": "-0.90"}
        raise AssertionError(f"unexpected order id {oid}")

    monkeypatch.setattr(cli_bridge, "_run", fake_run)

    async with storage_db.connect(db_path) as conn:
        report = await main_module.startup_reconcile(_reconcile_deps(db_path), conn)
        cur = await conn.execute(
            "SELECT status, final_order_id, filled_qty, cli_verified FROM trades WHERE id=?", (trade_id,)
        )
        row = await cur.fetchone()

    assert report.repaired == 1 and report.unresolved_transient == 0 and report.unresolved_position == 0
    assert tuple(row) == ("FILLED", "o1-r1", 2, 1)


async def test_reconcile_chain_cycle_is_bounded(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = str(tmp_path / "agent.db")
    await storage_db.init_db(db_path)
    async with storage_db.connect(db_path) as conn:
        await _seed(conn, status="ACCEPTED", order_id="o1", final_order_id="o1", qty=2)

    calls = {"n": 0}

    async def order_responder(args, *, timeout: float = 10.0):
        calls["n"] += 1
        return {"id": "o1", "status": "replaced", "replaced_by": "o1", "filled_qty": "0",
                 "filled_avg_price": None, "limit_price": "-0.90"}

    monkeypatch.setattr(cli_bridge, "_run", _fake_run_no_positions(order_responder))

    async with storage_db.connect(db_path) as conn:
        import asyncio
        report = await asyncio.wait_for(main_module.startup_reconcile(_reconcile_deps(db_path), conn), timeout=5.0)

    assert calls["n"] <= main_module.RECONCILE_MAX_CHAIN_HOPS
    assert report.unresolved_transient == 1  # no held position confirmed -> does not halt
    assert report.unresolved_position == 0


async def test_reconcile_never_decreases_filled_qty(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = str(tmp_path / "agent.db")
    await storage_db.init_db(db_path)
    async with storage_db.connect(db_path) as conn:
        trade_id = await _seed(conn, status="ACCEPTED", order_id="o1", final_order_id="o1", qty=2, filled_qty=2)

    async def fake_run(args, *, timeout: float = 10.0):
        return {"id": "o1", "status": "canceled", "replaced_by": None, "filled_qty": "1",
                 "filled_avg_price": "-0.90", "limit_price": "-0.90"}

    monkeypatch.setattr(cli_bridge, "_run", fake_run)

    async with storage_db.connect(db_path) as conn:
        await main_module.startup_reconcile(_reconcile_deps(db_path), conn)
        cur = await conn.execute("SELECT filled_qty, status FROM trades WHERE id=?", (trade_id,))
        row = await cur.fetchone()

    assert row[0] == 2  # never shrinks below the DB's own record
    assert row[1] == "PARTIAL_SUSPENDED"


async def test_reconcile_partial_becomes_open_trade(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercises B5 together with the PARTIALLY_FILLED branch of the B4 repair table."""
    db_path = str(tmp_path / "agent.db")
    await storage_db.init_db(db_path)
    plan = _plan(max_loss="210")
    async with storage_db.connect(db_path) as conn:
        await _seed(conn, status="ACCEPTED", order_id="o1", final_order_id="o1", qty=2, plan=plan)

    async def fake_run(args, *, timeout: float = 10.0):
        return {"id": "o1", "status": "partially_filled", "replaced_by": None, "filled_qty": "1",
                 "filled_avg_price": "-0.85", "limit_price": "-0.90"}

    monkeypatch.setattr(cli_bridge, "_run", fake_run)

    async with storage_db.connect(db_path) as conn:
        await main_module.startup_reconcile(_reconcile_deps(db_path), conn)
        open_trades = await main_module._open_trades(conn)
        risk = await main_module._open_defined_risk(conn)

    assert len(open_trades) == 1
    assert open_trades[0].qty == 1
    assert risk == Decimal("210")


async def test_reconcile_cancels_still_working_order(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = str(tmp_path / "agent.db")
    await storage_db.init_db(db_path)
    async with storage_db.connect(db_path) as conn:
        trade_id = await _seed(conn, status="ACCEPTED", order_id="o1", final_order_id="o1", qty=2)

    calls = {"n": 0}

    async def fake_run(args, *, timeout: float = 10.0):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"id": "o1", "status": "new", "replaced_by": None, "filled_qty": "0",
                     "filled_avg_price": None, "limit_price": "-0.90"}
        return {"id": "o1", "status": "canceled", "replaced_by": None, "filled_qty": "0",
                 "filled_avg_price": None, "limit_price": "-0.90"}

    monkeypatch.setattr(cli_bridge, "_run", fake_run)
    broker = MockBroker([])

    async with storage_db.connect(db_path) as conn:
        report = await main_module.startup_reconcile(_reconcile_deps(db_path, broker=broker), conn)
        cur = await conn.execute("SELECT status FROM trades WHERE id=?", (trade_id,))
        row = await cur.fetchone()

    assert broker.cancelled == ["o1"]
    assert report.cancelled_working == 1
    assert row[0] == "UNFILLED_REJECT"  # ends terminal, not left "still working"


async def test_reconcile_cancel_racing_a_fill_records_the_fill(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = str(tmp_path / "agent.db")
    await storage_db.init_db(db_path)
    async with storage_db.connect(db_path) as conn:
        trade_id = await _seed(conn, status="ACCEPTED", order_id="o1", final_order_id="o1", qty=2)

    calls = {"n": 0}

    async def fake_run(args, *, timeout: float = 10.0):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"id": "o1", "status": "accepted", "replaced_by": None, "filled_qty": "0",
                     "filled_avg_price": None, "limit_price": "-0.90"}
        return {"id": "o1", "status": "filled", "replaced_by": None, "filled_qty": "2",
                 "filled_avg_price": "-0.90", "limit_price": "-0.90"}

    monkeypatch.setattr(cli_bridge, "_run", fake_run)
    broker = MockBroker([])

    async with storage_db.connect(db_path) as conn:
        await main_module.startup_reconcile(_reconcile_deps(db_path, broker=broker), conn)
        cur = await conn.execute("SELECT status, filled_qty FROM trades WHERE id=?", (trade_id,))
        row = await cur.fetchone()

    assert broker.cancelled == ["o1"]
    assert tuple(row) == ("FILLED", 2)


async def test_reconcile_no_order_id_no_position_marks_unfilled(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = str(tmp_path / "agent.db")
    await storage_db.init_db(db_path)
    async with storage_db.connect(db_path) as conn:
        trade_id = await _seed(conn, status="NEW", order_id=None, final_order_id=None, qty=2)

    async def fake_list_positions():
        return []

    monkeypatch.setattr(cli_bridge, "list_positions", fake_list_positions)

    async with storage_db.connect(db_path) as conn:
        report = await main_module.startup_reconcile(_reconcile_deps(db_path), conn)
        cur = await conn.execute("SELECT status, filled_qty, cli_verified FROM trades WHERE id=?", (trade_id,))
        row = await cur.fetchone()

    assert report.repaired == 1 and report.unresolved_transient == 0 and report.unresolved_position == 0
    assert tuple(row) == ("UNFILLED_REJECT", 0, 1)


async def test_reconcile_no_order_id_with_live_position_is_unresolved(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = str(tmp_path / "agent.db")
    await storage_db.init_db(db_path)
    plan = _plan()
    async with storage_db.connect(db_path) as conn:
        trade_id = await _seed(conn, status="NEW", order_id=None, final_order_id=None, qty=2, plan=plan)

    async def fake_list_positions():
        return [cli_bridge.CliPosition(
            symbol=plan.legs[0].occ_symbol, asset_class="us_option", qty=Decimal("-2"),
            avg_entry_price=Decimal("1.0"), market_value=Decimal("-200"), unrealized_pl=Decimal("0"),
        )]

    monkeypatch.setattr(cli_bridge, "list_positions", fake_list_positions)

    async with storage_db.connect(db_path) as conn:
        report = await main_module.startup_reconcile(_reconcile_deps(db_path), conn)
        cur = await conn.execute("SELECT status FROM trades WHERE id=?", (trade_id,))
        row = await cur.fetchone()
        entries_halted = await main_module._read_state_value(conn, "entries_halted")

    assert row[0] == "NEW"  # untouched -- operator escalation, not auto-healed
    assert report.unresolved_position == 1
    assert report.unresolved_transient == 0
    assert entries_halted is True


async def test_reconcile_cli_unavailable_does_not_halt_entries(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A CLI outage confirms nothing either way -- transient, not a halt.
    scan_cycle's own CliUnavailable guard already blocks trading if the CLI
    is still down at scan time, so double-halting here would just leave a
    stale entries_halted flag an operator has to notice and clear by hand."""
    db_path = str(tmp_path / "agent.db")
    await storage_db.init_db(db_path)
    async with storage_db.connect(db_path) as conn:
        await _seed(conn, status="ACCEPTED", order_id="o1", final_order_id="o1", qty=2)

    async def fake_run(args, *, timeout: float = 10.0):
        raise cli_bridge.CliUnavailable("cli exploded")

    monkeypatch.setattr(cli_bridge, "_run", fake_run)

    async with storage_db.connect(db_path) as conn:
        report = await main_module.startup_reconcile(_reconcile_deps(db_path), conn)  # must not raise
        entries_halted = await main_module._read_state_value(conn, "entries_halted")

    assert report.unresolved_transient == 1
    assert report.unresolved_position == 0
    assert not entries_halted  # absent/False -- no halt


async def test_reconcile_unexplained_position_halts_entries(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A different unresolved path than the no-anchor case above (here: the
    order id is known but the CLI can't find it) still escalates to a halt
    once a held position is confirmed -- the position check applies at every
    unresolved site, not just the no-anchor one."""
    db_path = str(tmp_path / "agent.db")
    await storage_db.init_db(db_path)
    plan = _plan()
    async with storage_db.connect(db_path) as conn:
        trade_id = await _seed(conn, status="ACCEPTED", order_id="o1", final_order_id="o1", qty=2, plan=plan)

    async def fake_run(args, *, timeout: float = 10.0):
        if args[:2] == ["position", "list"]:
            return [{
                "symbol": plan.legs[0].occ_symbol, "asset_class": "us_option", "side": "short",
                "qty": "2", "avg_entry_price": "1.0", "market_value": "-200", "unrealized_pl": "0",
            }]
        return None  # `order get` -- CLI has no record of this order at all

    monkeypatch.setattr(cli_bridge, "_run", fake_run)

    async with storage_db.connect(db_path) as conn:
        report = await main_module.startup_reconcile(_reconcile_deps(db_path), conn)
        cur = await conn.execute("SELECT status FROM trades WHERE id=?", (trade_id,))
        row = await cur.fetchone()
        entries_halted = await main_module._read_state_value(conn, "entries_halted")

    assert row[0] == "ACCEPTED"  # untouched
    assert report.unresolved_position == 1
    assert report.unresolved_transient == 0
    assert entries_halted is True


async def test_reconcile_never_writes_realized_pnl_or_closed_at(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = str(tmp_path / "agent.db")
    await storage_db.init_db(db_path)
    async with storage_db.connect(db_path) as conn:
        trade_id = await _seed(conn, status="ACCEPTED", order_id="o1", final_order_id="o1", qty=2)

    async def fake_run(args, *, timeout: float = 10.0):
        return {"id": "o1", "status": "filled", "replaced_by": None, "filled_qty": "2",
                 "filled_avg_price": "-0.90", "limit_price": "-0.90"}

    monkeypatch.setattr(cli_bridge, "_run", fake_run)

    async with storage_db.connect(db_path) as conn:
        await main_module.startup_reconcile(_reconcile_deps(db_path), conn)
        cur = await conn.execute("SELECT closed_at, realized_pnl FROM trades WHERE id=?", (trade_id,))
        row = await cur.fetchone()

    assert row[0] is None
    assert row[1] is None


async def test_reconcile_unknown_status_without_position_is_transient(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """S3.3: unlike the SDK path (broker._order_state_from_sdk, which
    defaults an unknown status to ACCEPTED for live-trading compatibility),
    the reconcile must never guess a terminal state for an unmapped status --
    but with no held position confirmed, it's transient, not a halt."""
    db_path = str(tmp_path / "agent.db")
    await storage_db.init_db(db_path)
    async with storage_db.connect(db_path) as conn:
        trade_id = await _seed(conn, status="ACCEPTED", order_id="o1", final_order_id="o1", qty=2)

    async def order_responder(args, *, timeout: float = 10.0):
        return {"id": "o1", "status": "some_future_status_we_dont_know", "replaced_by": None,
                 "filled_qty": "0", "filled_avg_price": None, "limit_price": "-0.90"}

    monkeypatch.setattr(cli_bridge, "_run", _fake_run_no_positions(order_responder))

    async with storage_db.connect(db_path) as conn:
        report = await main_module.startup_reconcile(_reconcile_deps(db_path), conn)
        cur = await conn.execute("SELECT status FROM trades WHERE id=?", (trade_id,))
        row = await cur.fetchone()
        entries_halted = await main_module._read_state_value(conn, "entries_halted")

    assert report.unresolved_transient == 1
    assert report.unresolved_position == 0
    assert row[0] == "ACCEPTED"  # untouched
    assert not entries_halted


async def test_entries_halted_survives_management_tick(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """THE regression this fix exists for: management_tick unconditionally
    overwrites 'reduce_only' from the greeks breach every cycle (main.py's
    own management_tick), which used to silently clear startup_reconcile's
    halt within one MANAGEMENT_INTERVAL_S. entries_halted is a separate key
    management_tick never touches, so it must survive."""
    db_path = str(tmp_path / "agent.db")
    await storage_db.init_db(db_path)
    _patch_cli(monkeypatch, positions=[])  # unbreached greeks -- reduce_only recomputes to False

    async with storage_db.connect(db_path) as conn:
        await storage_write.put_state(conn, "entries_halted", True)

    clients = FakeClients()
    deps = _deps(db_path, clients, MockBroker([]), _FastClock(NOW))
    session = await main_module.current_or_next_session(clients)

    await main_module.management_tick(deps, session)

    async with storage_db.connect(db_path) as conn:
        entries_halted = await main_module._read_state_value(conn, "entries_halted")
        reduce_only = await main_module._read_state_value(conn, "reduce_only")

    assert entries_halted is True   # survived the tick
    assert reduce_only is False     # management_tick's own key did recompute


async def test_scan_cycle_rejects_entries_when_entries_halted(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = str(tmp_path / "agent.db")
    await storage_db.init_db(db_path)
    _patch_cli(monkeypatch)

    import agent.strategy.ticker_screener as ticker_screener_module
    from agent.strategy.regime import RegimeDecision
    from agent.strategy.regime import select as real_select

    def forced_select(q, assigned, skew_threshold, vwm_bar):
        if q.symbol == "SPY" and q.data_ok:
            return RegimeDecision(Regime.CREDIT, Structure.BULL_PUT_SPREAD, "forced", "TEST", None, None)
        return real_select(q, assigned, skew_threshold, vwm_bar)

    monkeypatch.setattr(main_module, "select", forced_select)
    monkeypatch.setattr(ticker_screener_module, "select", forced_select)

    async with storage_db.connect(db_path) as conn:
        await storage_write.put_state(conn, "entries_halted", True)

    clients = FakeClients()
    deps = _deps(db_path, clients, MockBroker([]), _FastClock(NOW))
    session = await main_module.current_or_next_session(clients)

    await main_module.scan_cycle(deps, session, dry_run=True)

    async with storage_db.connect(db_path) as conn:
        cur = await conn.execute("SELECT gate_reason FROM decisions WHERE symbol='SPY'")
        gate_reason = (await cur.fetchone())[0]

    assert gate_reason == "REDUCE_ONLY"


async def test_entries_halted_session_is_scoped_not_sticky(tmp_path) -> None:
    """docs/review.md P1-2: unlike the startup_reconcile 'entries_halted' key
    (sticky, needs an operator to clear it), the post-fill risk-breach halt
    writes 'entries_halted_session' = the session_date that tripped it, and
    _entries_halted must only honour it for THAT session -- ordinary
    credit-walk slippage must not silently disable entries for the rest of
    the competition once the session rolls over."""
    db_path = str(tmp_path / "agent.db")
    await storage_db.init_db(db_path)

    async with storage_db.connect(db_path) as conn:
        await storage_write.put_state(conn, "entries_halted_session", "2026-09-02")

        assert await main_module._entries_halted(conn, "2026-09-02") is True
        assert await main_module._entries_halted(conn, "2026-09-03") is False


async def test_entries_halted_ors_sticky_and_session_scoped_keys(tmp_path) -> None:
    """The sticky startup_reconcile halt must still block regardless of
    session_date -- session-scoping only applies to the newer key."""
    db_path = str(tmp_path / "agent.db")
    await storage_db.init_db(db_path)

    async with storage_db.connect(db_path) as conn:
        await storage_write.put_state(conn, "entries_halted", True)

        assert await main_module._entries_halted(conn, "2026-09-02") is True
        assert await main_module._entries_halted(conn, "2026-09-03") is True


class _SimulatedCrash(BaseException):
    """Deliberately NOT an Exception subclass -- must escape walk_to_fill's
    own bare `except Exception`, exactly as a kill -9 would."""


async def test_mid_walk_restart_reconstructs_filled_position(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """THE regression test for §0.1: a container restart between the SUBMIT
    write and the first REPLACE write must not orphan a filled position."""
    from agent.agents.pipeline import (
        DebateArtifact,
        DebateSummaryArtifact,
        PipelineArtifacts,
        PipelineOutcome,
        ProposalArtifact,
        RiskVoteArtifact,
    )
    from agent.execution.broker import OrderState
    from agent.schemas.execution import OrderStatus as _OrderStatus
    from agent.strategy.regime import RegimeDecision

    import agent.strategy.ticker_screener as ticker_screener_module

    db_path = str(tmp_path / "agent.db")
    await storage_db.init_db(db_path)
    _patch_cli(monkeypatch)

    def forced_select(q, assigned, skew_threshold, vwm_bar):
        # Force SPY as the only candidate -- everything else is NO_TRADE
        # regardless of assign_regimes' real cross-sectional output, so this
        # test's single-trade risk assertion stays independent of UNIVERSE
        # size (docs/day4_action_plan.md Step 7 widened it to 50, and AMD's
        # real fixture data would otherwise legitimately enter CREDIT via
        # real_select and place a second, unrelated trade in this cycle).
        if q.symbol == "SPY" and q.data_ok:
            return RegimeDecision(Regime.CREDIT, Structure.BULL_PUT_SPREAD, "forced", "TEST", None, None)
        return RegimeDecision(Regime.NO_TRADE, None, "forced-no-trade", "TEST", None, None)

    monkeypatch.setattr(main_module, "select", forced_select)
    monkeypatch.setattr(ticker_screener_module, "select", forced_select)
    monkeypatch.setattr(main_module, "fetch_headlines", lambda *a, **k: _immediate({}))
    monkeypatch.setattr(main_module, "_fetch_reddit", lambda *a, **k: _immediate({}))

    plan = SpreadPlan(
        symbol="SPY", structure=Structure.BULL_PUT_SPREAD, regime=Regime.CREDIT,
        expiry=date(2026, 9, 4), dte=4,
        legs=(
            Leg(occ_symbol="SPY260904P00772000", strike=772.0, right="P", side="SELL", ratio_qty=1,
                intent=Intent.SELL_TO_OPEN, delta=-0.275, vega=0.05, bid=1.0, ask=1.1),
            Leg(occ_symbol="SPY260904P00763000", strike=763.0, right="P", side="BUY", ratio_qty=1,
                intent=Intent.BUY_TO_OPEN, delta=-0.10, vega=0.03, bid=0.2, ask=0.3),
        ),
        width=9.0, net_mid=Decimal("-0.90"), net_natural=Decimal("-0.75"),
        max_profit_per_spread=Decimal("90"), max_loss_per_spread=Decimal("210"),
        p_success=0.72, spot=100.0, short_leg_delta=0.275,
    )
    outcome = PipelineOutcome(
        symbol="SPY", plan=plan, mode="llm", reason="OK", analyst_score=0.81, conviction=1.0,
        artifacts=PipelineArtifacts(
            debate_nodes=(
                DebateArtifact(round=1, persona="BULL", doc_action="COMMIT", evidence_cited_json="[]",
                               volatility_view="v", rebuttal_argument="r"),
                DebateArtifact(round=1, persona="BEAR", doc_action="COMMIT", evidence_cited_json="[]",
                               volatility_view="v", rebuttal_argument="r"),
            ),
            debate_summary=DebateSummaryArtifact(rounds_run=1, consensus_score=0.9,
                                                  verdict="CONSENSUS_ROUND_1", terminated_early=True),
            proposal_row=ProposalArtifact(proposal_json='{"confidence_score": 0.8}', accepted=True, reject_reason=None),
            risk_rows=(RiskVoteArtifact(persona="AGGRESSIVE", decision="APPROVE", max_loss_acceptable=True,
                                         risk_reward_ratio_acceptable=True, manager_notes="x"),),
        ),
    )

    async def fake_run_llm_pipeline(*args, **kwargs):
        return [outcome]

    monkeypatch.setattr(main_module, "run_llm_pipeline", fake_run_llm_pipeline)

    # Real update_trade_order_id runs for step 0 (SUBMIT); step 1 (the first
    # REPLACE) crashes instead of writing -- exactly what a kill -9 between
    # two walk steps looks like.
    real_update = storage_write.update_trade_order_id
    calls = {"n": 0}

    async def crashing_update(conn, trade_id, *, order_id, step):
        calls["n"] += 1
        if calls["n"] == 1:
            await real_update(conn, trade_id, order_id=order_id, step=step)
            return
        raise _SimulatedCrash()

    monkeypatch.setattr(storage_write, "update_trade_order_id", crashing_update)

    broker = MockBroker([
        OrderState(order_id="o1", status=_OrderStatus.NEW, limit_price=None, filled_qty=0, total_qty=2,
                   fill_avg_price=None, reject_code=None, reject_message=None),
    ])
    clock = _FastClock(NOW)
    clients = FakeClients()
    deps = _deps(db_path, clients, broker, clock)
    deps.llm_enabled = True
    deps.http = object()  # never dereferenced: run_llm_pipeline is monkeypatched above

    session = await main_module.current_or_next_session(clients)

    with pytest.raises(_SimulatedCrash):
        await main_module.scan_cycle(deps, session, dry_run=False)

    # Act 2: the bug, reproduced -- a filled-at-the-broker position the DB
    # thinks is still 'ACCEPTED' with filled_qty=0 is invisible to exit_tick.
    async with storage_db.connect(db_path) as conn:
        cur = await conn.execute("SELECT id, status, filled_qty, qty, order_id, final_order_id FROM trades")
        trade_id, status, filled_qty, qty, order_id, final_order_id = await cur.fetchone()
        assert (status, filled_qty, order_id, final_order_id) == ("ACCEPTED", 0, "o1", "o1")
        assert await main_module._open_trades(conn) == []

    # Act 3: the broker's own replace chain shows the order was replaced once
    # more and then filled -- reconcile must walk from the anchor to the head.
    async def fake_run(args, *, timeout: float = 10.0):
        oid = args[3]
        if oid == "o1":
            return {"id": "o1", "status": "replaced", "replaced_by": "o1-r1", "filled_qty": "0",
                     "filled_avg_price": None, "limit_price": "-0.90"}
        if oid == "o1-r1":
            return {"id": "o1-r1", "status": "filled", "replaced_by": None, "filled_qty": str(qty),
                     "filled_avg_price": "-0.90", "limit_price": "-0.90"}
        raise AssertionError(f"unexpected order id {oid}")

    monkeypatch.setattr(cli_bridge, "_run", fake_run)

    async with storage_db.connect(db_path) as conn:
        report = await main_module.startup_reconcile(deps, conn)
        cur = await conn.execute(
            "SELECT status, filled_qty, cli_verified, final_order_id FROM trades WHERE id=?", (trade_id,)
        )
        row = await cur.fetchone()
        open_trades = await main_module._open_trades(conn)
        risk = await main_module._open_defined_risk(conn)

    assert report.repaired == 1 and report.unresolved_transient == 0 and report.unresolved_position == 0
    assert tuple(row) == ("FILLED", qty, 1, "o1-r1")
    assert len(open_trades) == 1
    assert risk == plan.max_loss_per_spread * qty

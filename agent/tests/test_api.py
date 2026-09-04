from __future__ import annotations

import ast
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from agent.storage import db as storage_db
from agent.storage import write as storage_write

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_api_is_get_only() -> None:
    from agent.api.app import app

    methods = {m for r in app.routes for m in getattr(r, "methods", set())}
    assert methods <= {"GET", "HEAD"}


def test_api_import_graph() -> None:
    src = (REPO_ROOT / "agent" / "api" / "app.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    banned_prefixes = ("agent.storage.write", "agent.execution", "agent.risk")
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert not node.module.startswith(banned_prefixes), node.module
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith(banned_prefixes), alias.name


async def _seed(db_path: str, cycle_id: str) -> None:
    async with storage_db.connect(db_path) as conn:
        row = storage_write.DecisionRow(
            ts_utc=datetime.now(timezone.utc).isoformat(), cycle_id=cycle_id,
            session_date=date(2026, 8, 31).isoformat(), symbol="SPY", mode="quant-only",
            regime="CREDIT", structure="BULL_PUT_SPREAD", action="ENTER", gate_reason="APPROVED",
            gate_detail="APPROVED", observed_value=None, threshold_value=None, qty=6,
            equity_feed="iex", earnings_armed=False, quant_json="{}", plan_json=None,
        )
        await storage_write.insert_decision(conn, row)
        await storage_write.put_state(conn, "account", {"equity": "100000"})
        await storage_write.put_state(conn, "positions", [])
        await storage_write.put_state(conn, "last_cycle", {"cycle_id": cycle_id})


async def test_api_serves_persisted_state(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Seeded DB -> /decisions and /state/account return rows with NO broker
    or CLI call (both monkeypatched to raise)."""
    db_path = str(tmp_path / "test_agent.db")
    await storage_db.init_db(db_path)
    await _seed(db_path, "cyc-1")

    def _raise(*args, **kwargs):
        raise AssertionError("API must never touch the broker or the CLI")

    monkeypatch.setattr("agent.execution.cli_bridge._run", _raise)
    monkeypatch.setattr("agent.execution.alpaca_client.AlpacaClients.__init__", _raise)

    from agent.api import app as api_app

    async with storage_db.connect(db_path) as conn:
        decisions = await api_app.decisions(limit=10, conn=conn)
        assert len(decisions) == 1
        assert decisions[0]["symbol"] == "SPY"

        health = await api_app.health(conn=conn)
        assert health["ok"] is True
        assert health["last_cycle_utc"] is not None

        account = await api_app.state_account(conn=conn)
        assert account["equity"] == "100000"

        greeks = await api_app.greeks_latest(conn=conn)
        assert greeks == {}

        detail = await api_app.decision_detail(decisions[0]["id"], conn=conn)
        assert detail["decision"]["symbol"] == "SPY"


async def test_decision_chain_serves_full_chain(tmp_path) -> None:
    """docs/day3_llm_plan.md Group 5: /decisions/{id} returns all seven
    sections of the reasoning chain in one request."""
    db_path = str(tmp_path / "test_agent.db")
    await storage_db.init_db(db_path)

    async with storage_db.connect(db_path) as conn:
        decision_id = await storage_write.insert_decision(conn, storage_write.DecisionRow(
            ts_utc=datetime.now(timezone.utc).isoformat(), cycle_id="cyc-2",
            session_date=date(2026, 8, 31).isoformat(), symbol="SPY", mode="llm",
            regime="CREDIT", structure="BULL_PUT_SPREAD", action="ENTER", gate_reason="APPROVED",
            gate_detail="APPROVED", observed_value=None, threshold_value=None, qty=6,
            equity_feed="iex", earnings_armed=False, quant_json="{}", plan_json=None,
        ))
        ts = datetime.now(timezone.utc).isoformat()
        await storage_write.insert_analyst_output(conn, storage_write.AnalystOutputRow(
            decision_id=decision_id, ts_utc=ts, symbol="SPY", analyst="QUANT", ok=True,
            output_json="{}", error=None,
        ))
        await storage_write.insert_debate(conn, storage_write.DebateRow(
            decision_id=decision_id, ts_utc=ts, round=1, persona="BULL", doc_action="COMMIT",
            evidence_cited_json="[]", volatility_view="v", rebuttal_argument="r",
        ))
        await storage_write.insert_debate_summary(conn, storage_write.DebateSummaryRow(
            decision_id=decision_id, ts_utc=ts, rounds_run=1, consensus_score=0.9,
            verdict="CONSENSUS_ROUND_1", terminated_early=True,
        ))
        await storage_write.insert_proposal(conn, storage_write.ProposalRow(
            decision_id=decision_id, ts_utc=ts, proposal_json="{}", accepted=True, reject_reason=None,
        ))
        await storage_write.insert_risk_vote(conn, storage_write.RiskVoteRow(
            decision_id=decision_id, ts_utc=ts, persona="AGGRESSIVE", decision="APPROVE",
            max_loss_acceptable=True, risk_reward_ratio_acceptable=True, manager_notes="x",
        ))
        trade_id = await storage_write.insert_trade(conn, storage_write.TradeRow(
            decision_id=decision_id, ts_utc=ts, symbol="SPY", structure="BULL_PUT_SPREAD",
            expiry="2026-09-04", legs_json="[]", qty=6, submitted_limit=Decimal("-0.9"),
        ))
        call_id = await storage_write.insert_llm_call(conn, storage_write.LlmCallRow(
            ts_utc=ts, node="QUANT", provider="featherless", model="m", prompt_tokens=1,
            completion_tokens=1, latency_ms=1, est_cost_usd=Decimal("0.001"), ok=True,
        ))
        await storage_write.update_llm_calls_decision_id(conn, [call_id], decision_id)

    from agent.api import app as api_app

    async with storage_db.connect(db_path) as conn:
        detail = await api_app.decision_detail(decision_id, conn=conn)

    assert detail["decision"]["symbol"] == "SPY"
    assert len(detail["analyst_outputs"]) == 1
    assert len(detail["debates"]) == 1
    assert detail["debate_summary"]["verdict"] == "CONSENSUS_ROUND_1"
    assert detail["proposal"]["accepted"] == 1
    assert len(detail["risk_votes"]) == 1
    assert len(detail["trades"]) == 1
    assert len(detail["llm_calls"]) == 1
    assert detail["llm_calls"][0]["decision_id"] == decision_id


async def test_decision_chain_attaches_walk_cap_from_plan(tmp_path) -> None:
    """docs/review.md Task 4: /decisions/{id} attaches a `walk_cap` to each
    trade, computed by the real agent.tools.walk_cap.walk_cap() (the same
    function order_manager._walk calls) from the decision's plan_json --
    never re-derived from scratch, so the M3 walk-timeline chart and the live
    walk can never disagree. Numbers are the real 09-01 LLY trade (decision
    149 in production): mid=1.94, natural=8.84, width=5.0, opening (BUY_TO_OPEN/
    SELL_TO_OPEN) BEAR_PUT_SPREAD -> is_closing=False -> the P0-2/P0-3-clamped
    cap of $3.00 (width * WALK_CAP_MAX_FRACTION_OF_WIDTH == 5.0 * 0.60), not
    the pre-remediation unclamped $6.77 (mid + WALK_CAP_FRACTION*(natural-mid))."""
    db_path = str(tmp_path / "test_agent.db")
    await storage_db.init_db(db_path)

    plan_json = (
        '{"symbol":"LLY","structure":"BEAR_PUT_SPREAD","regime":"DEBIT","expiry":"2026-09-04","dte":3,'
        '"legs":[{"occ_symbol":"LLY260904P01160000","strike":1160.0,"right":"P","side":"SELL",'
        '"ratio_qty":1,"intent":"SELL_TO_OPEN","delta":-0.4385,"vega":0.4162,"bid":8.9,"ask":15.09},'
        '{"occ_symbol":"LLY260904P01165000","strike":1165.0,"right":"P","side":"BUY","ratio_qty":1,'
        '"intent":"BUY_TO_OPEN","delta":-0.4938,"vega":0.4212,"bid":10.13,"ask":17.74}],'
        '"width":5.0,"net_mid":"1.94","net_natural":"8.84","max_profit_per_spread":"306.00",'
        '"max_loss_per_spread":"194.00","p_success":0.4744989445350674,"spot":1164.8,'
        '"short_leg_delta":0.4385}'
    )
    async with storage_db.connect(db_path) as conn:
        decision_id = await storage_write.insert_decision(conn, storage_write.DecisionRow(
            ts_utc=datetime.now(timezone.utc).isoformat(), cycle_id="cyc-lly",
            session_date=date(2026, 9, 1).isoformat(), symbol="LLY", mode="llm",
            regime="DEBIT", structure="BEAR_PUT_SPREAD", action="ENTER", gate_reason="APPROVED",
            gate_detail="APPROVED", observed_value=None, threshold_value=None, qty=4,
            equity_feed="iex", earnings_armed=True, quant_json="{}", plan_json=plan_json,
        ))
        await storage_write.insert_trade(conn, storage_write.TradeRow(
            decision_id=decision_id, ts_utc=datetime.now(timezone.utc).isoformat(), symbol="LLY",
            structure="BEAR_PUT_SPREAD", expiry="2026-09-04", legs_json="[]", qty=4,
            submitted_limit=Decimal("1.94"),
        ))

    from agent.api import app as api_app

    async with storage_db.connect(db_path) as conn:
        detail = await api_app.decision_detail(decision_id, conn=conn)

    assert len(detail["trades"]) == 1
    # decision_detail is called directly here (not through FastAPI's routing),
    # so walk_cap arrives as the raw Decimal read.py returns -- the HTTP path's
    # jsonable_encoder is what turns it into a JSON float for the frontend.
    assert detail["trades"][0]["walk_cap"] == Decimal("3.00")


async def test_assignments_endpoint_serves_rows(tmp_path) -> None:
    db_path = str(tmp_path / "test_agent.db")
    await storage_db.init_db(db_path)

    async with storage_db.connect(db_path) as conn:
        await storage_write.insert_assignment_event(conn, storage_write.AssignmentEventRow(
            ts_utc=datetime.now(timezone.utc).isoformat(), session_date=date(2026, 8, 31).isoformat(),
            symbol="AAPL", trade_id=None, reason="SHORT_CALL_ASSIGNED", assigned_right="C",
            equity_qty=-100, contracts=1, equity_status="FLATTENED", equity_order_id="o1",
            equity_fill_price=Decimal("180.42"), orphan_occ_symbol="AAPL260904C00190000",
            orphan_qty=1, orphan_status="FLATTENED", orphan_order_id="o2",
            orphan_fill_price=Decimal("0.13"), detail="test",
        ))

    from agent.api import app as api_app

    async with storage_db.connect(db_path) as conn:
        rows = await api_app.assignments(limit=10, conn=conn)
    assert len(rows) == 1
    assert rows[0]["symbol"] == "AAPL"
    assert rows[0]["equity_fill_price"] == pytest.approx(180.42)


async def test_status_endpoint_serves_published_state(tmp_path) -> None:
    db_path = str(tmp_path / "test_agent.db")
    await storage_db.init_db(db_path)

    async with storage_db.connect(db_path) as conn:
        await storage_write.put_state(conn, "status", {
            "live": True, "llm_enabled": True, "is_open": False,
            "next_action": "market open", "next_action_utc": "2026-08-31T13:30:00+00:00",
        })

    from agent.api import app as api_app

    async with storage_db.connect(db_path) as conn:
        status = await api_app.status(conn=conn)
    assert status["live"] is True
    assert status["next_action"] == "market open"


async def test_status_endpoint_empty_before_first_publish(tmp_path) -> None:
    db_path = str(tmp_path / "test_agent.db")
    await storage_db.init_db(db_path)

    from agent.api import app as api_app

    async with storage_db.connect(db_path) as conn:
        status = await api_app.status(conn=conn)
    assert status == {}


async def _insert_greeks(conn, ts_utc: str, equity: float) -> None:
    await storage_write.insert_greeks_snapshot(conn, storage_write.GreeksRow(
        ts_utc=ts_utc, equity=equity, delta_dollars=1000.0, vega_dollars=200.0,
        delta_limit=15000.0, vega_limit=2000.0, breached=False, per_position_json="[]",
    ))


async def test_equity_history_endpoint_returns_ordered_series(tmp_path) -> None:
    db_path = str(tmp_path / "test_agent.db")
    await storage_db.init_db(db_path)

    async with storage_db.connect(db_path) as conn:
        await _insert_greeks(conn, "2026-08-31T14:00:00Z", 100500.0)
        await _insert_greeks(conn, "2026-08-31T13:00:00Z", 100000.0)

    from agent.api import app as api_app

    async with storage_db.connect(db_path) as conn:
        rows = await api_app.equity_history(limit=500, conn=conn)
    assert len(rows) == 2
    assert rows[0]["ts_utc"] == "2026-08-31T13:00:00Z"  # oldest first
    assert rows[0]["equity"] == pytest.approx(100000.0)
    assert rows[1]["equity"] == pytest.approx(100500.0)


async def test_greeks_history_endpoint_returns_full_rows_ordered(tmp_path) -> None:
    db_path = str(tmp_path / "test_agent.db")
    await storage_db.init_db(db_path)

    async with storage_db.connect(db_path) as conn:
        await _insert_greeks(conn, "2026-08-31T14:00:00Z", 100500.0)
        await _insert_greeks(conn, "2026-08-31T13:00:00Z", 100000.0)

    from agent.api import app as api_app

    async with storage_db.connect(db_path) as conn:
        rows = await api_app.greeks_history(limit=500, conn=conn)
    assert len(rows) == 2
    assert rows[0]["ts_utc"] == "2026-08-31T13:00:00Z"
    assert rows[1]["delta_dollars"] == pytest.approx(1000.0)


async def test_positions_open_endpoint_excludes_closed_and_joins_live_legs(tmp_path) -> None:
    db_path = str(tmp_path / "test_agent.db")
    await storage_db.init_db(db_path)

    async with storage_db.connect(db_path) as conn:
        decision_id = await storage_write.insert_decision(conn, storage_write.DecisionRow(
            ts_utc=datetime.now(timezone.utc).isoformat(), cycle_id="cyc-3",
            session_date=date(2026, 8, 31).isoformat(), symbol="SPY", mode="quant-only",
            regime="CREDIT", structure="BULL_PUT_SPREAD", action="ENTER", gate_reason="APPROVED",
            gate_detail="APPROVED", observed_value=None, threshold_value=None, qty=6,
            equity_feed="iex", earnings_armed=False, quant_json="{}", plan_json=None,
        ))
        # P2 remediation (docs/audit_report_v2.md §9 item 11): an "open"
        # position must actually be filled -- status FILLED, filled_qty > 0 --
        # not merely closed_at IS NULL (that predicate alone also matches an
        # UNFILLED_REJECT row with no broker position at all).
        open_trade_id = await storage_write.insert_trade(conn, storage_write.TradeRow(
            decision_id=decision_id, ts_utc=datetime.now(timezone.utc).isoformat(), symbol="SPY",
            structure="BULL_PUT_SPREAD", expiry="2026-09-04", legs_json="[]", qty=6,
            submitted_limit=Decimal("-0.9"), status="FILLED", filled_qty=6,
        ))
        await storage_write.insert_trade(conn, storage_write.TradeRow(
            decision_id=decision_id, ts_utc=datetime.now(timezone.utc).isoformat(), symbol="SPY",
            structure="BULL_PUT_SPREAD", expiry="2026-09-04", legs_json="[]", qty=6,
            submitted_limit=Decimal("-0.9"), closed_at=datetime.now(timezone.utc).isoformat(),
            status="FILLED", filled_qty=6,
        ))
        # Regression case for the bug itself: an UNFILLED_REJECT row with no
        # broker position, closed_at still NULL -- must NOT be reported open.
        await storage_write.insert_trade(conn, storage_write.TradeRow(
            decision_id=decision_id, ts_utc=datetime.now(timezone.utc).isoformat(), symbol="SPY",
            structure="BULL_PUT_SPREAD", expiry="2026-09-04", legs_json="[]", qty=6,
            submitted_limit=Decimal("-0.9"), status="UNFILLED_REJECT", filled_qty=0,
        ))
        await storage_write.insert_greeks_snapshot(conn, storage_write.GreeksRow(
            ts_utc=datetime.now(timezone.utc).isoformat(), equity=100000.0, delta_dollars=1000.0,
            vega_dollars=200.0, delta_limit=15000.0, vega_limit=2000.0, breached=False,
            per_position_json='[{"occ_symbol": "SPY260904P00615000", "underlying": "SPY", '
                               '"expiry": "2026-09-04", "qty": -6, "delta": -0.27, "vega": 8.1, "spot": 640.0}]',
        ))

    from agent.api import app as api_app

    async with storage_db.connect(db_path) as conn:
        rows = await api_app.positions_open(conn=conn)
    assert len(rows) == 1
    assert rows[0]["id"] == open_trade_id
    assert rows[0]["live_legs"][0]["underlying"] == "SPY"
    assert rows[0]["live_legs"][0]["delta"] == pytest.approx(-0.27)


async def test_funnel_endpoint_buckets_by_stage(tmp_path) -> None:
    db_path = str(tmp_path / "test_agent.db")
    await storage_db.init_db(db_path)
    session_date = date(2026, 8, 31).isoformat()

    async def _row(symbol: str, mode: str, gate_reason: str, action: str) -> storage_write.DecisionRow:
        return storage_write.DecisionRow(
            ts_utc=datetime.now(timezone.utc).isoformat(), cycle_id="cyc-4",
            session_date=session_date, symbol=symbol, mode=mode, regime="CREDIT",
            structure=None, action=action, gate_reason=gate_reason, gate_detail=gate_reason,
            observed_value=None, threshold_value=None, qty=None, equity_feed="iex",
            earnings_armed=False, quant_json="{}", plan_json=None,
        )

    async with storage_db.connect(db_path) as conn:
        await storage_write.insert_decision(conn, await _row("QQQ", "quant-only", "NO_REGIME", "NO_TRADE"))
        debated_id = await storage_write.insert_decision(conn, await _row("AAPL", "llm", "MAX_RISK_PER_TRADE", "NO_TRADE"))
        entered_id = await storage_write.insert_decision(conn, await _row("SPY", "llm", "APPROVED", "ENTER"))
        ts = datetime.now(timezone.utc).isoformat()
        for did in (debated_id, entered_id):
            await storage_write.insert_debate_summary(conn, storage_write.DebateSummaryRow(
                decision_id=did, ts_utc=ts, rounds_run=1, consensus_score=0.9,
                verdict="CONSENSUS_ROUND_1", terminated_early=True, conviction=1.0,
            ))

    from agent.api import app as api_app

    async with storage_db.connect(db_path) as conn:
        result = await api_app.funnel(session_date=session_date, conn=conn)

    by_name = {s["name"]: s for s in result["stages"]}
    assert by_name["screened"]["count"] == 3
    assert by_name["shortlisted"]["count"] == 2
    assert by_name["built"]["count"] == 2
    assert by_name["debated"]["count"] == 2
    assert by_name["entered"]["count"] == 1


async def test_funnel_endpoint_separates_build_failures_from_screen_and_gate_rejects(tmp_path) -> None:
    """docs/review.md P2-1: DEGENERATE_CHAIN/NO_CHAIN must count as a screen
    reject (they were previously counted as shortlisted), and a
    BuildFailure/ProposalFailure reason (a spread that could not be
    constructed) must land in the `built` stage's reject reason rather than
    being conflated with a deterministic gate reject like NEGATIVE_EDGE."""
    db_path = str(tmp_path / "test_agent.db")
    await storage_db.init_db(db_path)
    session_date = date(2026, 8, 31).isoformat()

    async def _row(symbol: str, mode: str, gate_reason: str, action: str) -> storage_write.DecisionRow:
        return storage_write.DecisionRow(
            ts_utc=datetime.now(timezone.utc).isoformat(), cycle_id="cyc-5",
            session_date=session_date, symbol=symbol, mode=mode, regime="CREDIT",
            structure=None, action=action, gate_reason=gate_reason, gate_detail=gate_reason,
            observed_value=None, threshold_value=None, qty=None, equity_feed="iex",
            earnings_armed=False, quant_json="{}", plan_json=None,
        )

    async with storage_db.connect(db_path) as conn:
        await storage_write.insert_decision(conn, await _row("SPY", "quant-only", "DEGENERATE_CHAIN", "NO_TRADE"))
        await storage_write.insert_decision(conn, await _row("AAPL", "quant-only", "STRUCTURE_MISMATCH", "NO_TRADE"))
        await storage_write.insert_decision(conn, await _row("TSLA", "quant-only", "NEGATIVE_EDGE", "NO_TRADE"))

    from agent.api import app as api_app

    async with storage_db.connect(db_path) as conn:
        result = await api_app.funnel(session_date=session_date, conn=conn)

    by_name = {s["name"]: s for s in result["stages"]}
    assert by_name["screened"]["count"] == 3
    assert by_name["shortlisted"]["count"] == 2  # DEGENERATE_CHAIN excluded at screen
    assert by_name["shortlisted"]["top_reject_reason"] == "STRUCTURE_MISMATCH"
    assert by_name["built"]["count"] == 1  # STRUCTURE_MISMATCH excluded as a build failure
    assert by_name["built"]["top_reject_reason"] == "NEGATIVE_EDGE"


async def test_funnel_endpoint_defaults_to_latest_session(tmp_path) -> None:
    db_path = str(tmp_path / "test_agent.db")
    await storage_db.init_db(db_path)

    async with storage_db.connect(db_path) as conn:
        await storage_write.insert_decision(conn, storage_write.DecisionRow(
            ts_utc="2026-08-31T14:00:00Z", cycle_id="cyc-5", session_date="2026-08-31",
            symbol="SPY", mode="quant-only", regime="NO_TRADE", structure=None, action="NO_TRADE",
            gate_reason="NO_REGIME", gate_detail="NO_REGIME", observed_value=None,
            threshold_value=None, qty=None, equity_feed="iex", earnings_armed=False,
            quant_json="{}", plan_json=None,
        ))

    from agent.api import app as api_app

    async with storage_db.connect(db_path) as conn:
        result = await api_app.funnel(session_date=None, conn=conn)
    assert result["session_date"] == "2026-08-31"


async def test_reflections_endpoint(tmp_path) -> None:
    db_path = str(tmp_path / "test_agent.db")
    await storage_db.init_db(db_path)

    async with storage_db.connect(db_path) as conn:
        await storage_write.insert_reflection(conn, storage_write.ReflectionRow(
            ts_utc=datetime.now(timezone.utc).isoformat(), session_date=date(2026, 8, 31).isoformat(),
            decisions_examined=10, binding_constraint="NO_REGIME", constraint_count=6,
            verdict="HOLD", argument="the screen blocked most names for a defensible reason",
            proposed_change=None, ok=True,
        ))

    from agent.api import app as api_app

    async with storage_db.connect(db_path) as conn:
        rows = await api_app.reflections(limit=10, conn=conn)
    assert len(rows) == 1
    assert rows[0]["session_date"] == "2026-08-31"
    assert rows[0]["binding_constraint"] == "NO_REGIME"
    assert rows[0]["verdict"] == "HOLD"


async def test_funnel_endpoint_empty_db(tmp_path) -> None:
    db_path = str(tmp_path / "test_agent.db")
    await storage_db.init_db(db_path)

    from agent.api import app as api_app

    async with storage_db.connect(db_path) as conn:
        result = await api_app.funnel(session_date=None, conn=conn)
    assert result["session_date"] is None
    assert all(s["count"] == 0 for s in result["stages"])


async def test_markgap_endpoint_wraps_state_with_its_write_time(tmp_path) -> None:
    """`asof` is load-bearing: after the unwind the book is flat and the panel
    has to say when the last non-zero reading was taken."""
    db_path = str(tmp_path / "test_agent.db")
    await storage_db.init_db(db_path)

    async with storage_db.connect(db_path) as conn:
        await storage_write.put_state(conn, "markgap", {
            "computed_at": "2026-09-03T14:52:00+00:00",
            "spreads": [{"trade_id": 8, "symbol": "LLY", "markgap": "-2140.00"}],
            "omitted": 0, "total_markgap": "-2140.00",
        })

    from agent.api import app as api_app

    async with storage_db.connect(db_path) as conn:
        payload = await api_app.markgap(conn=conn)
    assert payload["value"]["total_markgap"] == "-2140.00"
    assert payload["asof"]


async def test_markgap_endpoint_empty_before_first_publish(tmp_path) -> None:
    db_path = str(tmp_path / "test_agent.db")
    await storage_db.init_db(db_path)

    from agent.api import app as api_app

    async with storage_db.connect(db_path) as conn:
        assert await api_app.markgap(conn=conn) == {}


def test_cors_keeps_the_published_dashboard_when_web_origin_is_stale() -> None:
    """A renamed Vercel project must not be able to silently un-CORS the site.

    This has happened twice in production. The symptom is deceptive: every
    endpoint still answers 200 and /health looks healthy, because only the
    browser enforces the missing header -- so the failure shows up as an empty
    replay panel on the landing page and nowhere else.
    """
    from agent.config import WEB_ORIGINS_DEFAULT, cors_origins

    published = WEB_ORIGINS_DEFAULT[0]

    assert cors_origins("") == [published]
    assert published in cors_origins("https://a-previous-name.vercel.app")
    # Comma-separated, whitespace- and trailing-slash-tolerant, no duplicates.
    assert cors_origins(f" {published}/ , https://preview.example ") == [
        published,
        "https://preview.example",
    ]

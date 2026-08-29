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

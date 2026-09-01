from __future__ import annotations

import inspect
from datetime import datetime, timezone
from typing import Any

import pytest

from agent.agents import reflector
from agent.schemas.llm import ReflectorOutput
from agent.storage import db as storage_db
from agent.storage import write as storage_write
from agent.tools.llm import LlmUnavailable


def _row(
    gate_reason: str, *, observed: float | None = None, threshold: float | None = None,
    action: str = "NO_TRADE", session_date: str = "2026-08-31",
) -> dict[str, Any]:
    return {
        "gate_reason": gate_reason, "observed_value": observed, "threshold_value": threshold,
        "action": action, "session_date": session_date,
    }


def test_digest_identifies_binding_constraint() -> None:
    rows = [
        _row("NO_REGIME"), _row("NO_REGIME"), _row("NO_REGIME"),
        _row("DATA_NOT_OK"), _row("DATA_NOT_OK"),
        _row("APPROVED", action="ENTER"),
    ]
    d = reflector.digest(rows)
    assert d.binding_constraint == "NO_REGIME"
    assert d.constraint_count == 3
    assert d.decisions_examined == 6
    assert d.entered == 1


def test_digest_ties_break_on_first_appearance() -> None:
    rows = [_row("DATA_NOT_OK"), _row("NO_REGIME"), _row("DATA_NOT_OK"), _row("NO_REGIME")]
    d = reflector.digest(rows)
    assert d.binding_constraint == "DATA_NOT_OK"  # appeared first, tied 2-2


def test_digest_observed_range_and_threshold_scoped_to_binding_reason() -> None:
    rows = [
        _row("DEBIT_NO_MOMENTUM_CONFIRMATION", observed=0.30, threshold=0.75),
        _row("DEBIT_NO_MOMENTUM_CONFIRMATION", observed=0.55, threshold=0.75),
        _row("NO_REGIME", observed=99.0, threshold=1.0),  # must not leak into the binding reason's range
    ]
    d = reflector.digest(rows)
    assert d.binding_constraint == "DEBIT_NO_MOMENTUM_CONFIRMATION"
    assert d.observed_range == (0.30, 0.55)
    assert d.threshold == pytest.approx(0.75)


def test_digest_pure_no_io() -> None:
    assert not inspect.iscoroutinefunction(reflector.digest)
    # Takes rows, not a connection -- a plain list argument must not raise.
    reflector.digest([_row("NO_REGIME")])


def test_digest_excludes_denylisted_reasons_from_binding_constraint() -> None:
    """P1 remediation (docs/audit_report_v2.md §7c/§9 item 9): the 2026-09-01
    reflection recommended loosening DEGENERATE_CHAIN -- the only liquidity
    guardrail -- from rejection counts alone. DEGENERATE_CHAIN dominates this
    session's rows but must never be selected as the binding constraint;
    NO_REGIME (the next-most-common NON-denylisted reason) wins instead."""
    rows = [
        _row("DEGENERATE_CHAIN"), _row("DEGENERATE_CHAIN"), _row("DEGENERATE_CHAIN"),
        _row("NO_REGIME"), _row("NO_REGIME"),
    ]
    d = reflector.digest(rows)
    assert d.binding_constraint == "NO_REGIME"
    assert d.constraint_count == 2
    # The full histogram still carries the denylisted reason for context.
    assert dict(d.gate_histogram)["DEGENERATE_CHAIN"] == 3


def test_digest_returns_none_when_every_reason_is_denylisted() -> None:
    """No fallback to the next-most-common gate when ALL observed reasons are
    denylisted -- must be a null/no-verdict result, not a silent pick."""
    rows = [_row("DEGENERATE_CHAIN"), _row("MAX_QUOTE_SPREAD_PCT"), _row("NO_CHAIN")]
    d = reflector.digest(rows)
    assert d.binding_constraint is None
    assert d.constraint_count == 0
    assert d.observed_range is None
    assert d.threshold is None


async def test_reflect_returns_null_verdict_with_zero_llm_calls_when_all_denylisted() -> None:
    """reflect() must not call the LLM at all when digest() found no eligible
    binding constraint -- there is nothing non-denylisted to argue about."""
    rows = [_row("DEGENERATE_CHAIN"), _row("DEGENERATE_CHAIN")]
    d = reflector.digest(rows)
    assert d.binding_constraint is None

    llm = ScriptedLlm(_OUTPUT)
    result = await reflector.reflect(llm, d, sink=[])
    assert result.ok is False
    assert result.output is None
    assert result.digest is d
    assert llm.calls == []  # zero LLM calls


class ScriptedLlm:
    def __init__(self, result: ReflectorOutput | Exception) -> None:
        self._result = result
        self.calls: list[str] = []

    async def complete_json(self, prompt, schema, *, node, system=None, sink=None):
        self.calls.append(node)
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


_OUTPUT = ReflectorOutput(verdict="HOLD", argument="a" * 50, proposed_change=None)


async def test_reflect_single_call() -> None:
    llm = ScriptedLlm(_OUTPUT)
    d = reflector.digest([_row("NO_REGIME")])
    result = await reflector.reflect(llm, d, sink=[])
    assert result.ok is True
    assert result.output is _OUTPUT
    assert llm.calls == ["REFLECTOR"]


async def test_reflect_survives_llm_failure() -> None:
    llm = ScriptedLlm(LlmUnavailable("boom"))
    d = reflector.digest([_row("NO_REGIME")])
    result = await reflector.reflect(llm, d, sink=[])
    assert result.ok is False
    assert result.output is None
    assert result.digest is d


async def test_insert_reflection_idempotent(tmp_path) -> None:
    db_path = str(tmp_path / "agent.db")
    await storage_db.init_db(db_path)

    def _reflection_row() -> storage_write.ReflectionRow:
        return storage_write.ReflectionRow(
            ts_utc=datetime.now(timezone.utc).isoformat(), session_date="2026-08-31",
            decisions_examined=10, binding_constraint="NO_REGIME", constraint_count=6,
            verdict="HOLD", argument="the screen blocked most names for a defensible reason",
            proposed_change=None, ok=True,
        )

    async with storage_db.connect(db_path) as conn:
        first_id = await storage_write.insert_reflection(conn, _reflection_row())
        second_id = await storage_write.insert_reflection(conn, _reflection_row())
        assert first_id == second_id

        cur = await conn.execute("SELECT COUNT(*) FROM reflections")
        row = await cur.fetchone()
        assert row[0] == 1

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


def _trade(
    symbol: str, *, submitted_limit: float = 0.0, fill_price: float | None = None,
    realized_pnl: float | None = None, closed_at: str | None = None,
) -> dict[str, Any]:
    return {
        "symbol": symbol, "submitted_limit": submitted_limit, "fill_price": fill_price,
        "realized_pnl": realized_pnl, "closed_at": closed_at,
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


def test_digest_defaults_outcome_block_when_no_trades_passed() -> None:
    """docs/review.md Task 7: every existing call site (and every session
    before Thursday's unwind) passes no trades at all -- must digest exactly
    as before this block existed, not raise or silently pick garbage."""
    d = reflector.digest([_row("NO_REGIME")])
    assert d.closed_trades == 0
    assert d.realized_pnl == 0.0
    assert d.wins == 0
    assert d.avg_slippage_vs_mid == 0.0
    assert d.worst_trade is None


def test_digest_outcome_block_scopes_pnl_to_closed_trades_only() -> None:
    """realized_pnl/wins/worst_trade must ignore a still-open trade (closed_at
    is None) even though it has a fill_price -- only closed_at rows have a
    meaningful realized_pnl at all."""
    trades = [
        _trade("SPY", submitted_limit=-0.90, fill_price=-0.85, realized_pnl=120.0, closed_at="t1"),
        _trade("TSLA", submitted_limit=1.50, fill_price=1.60, realized_pnl=-300.0, closed_at="t2"),
        _trade("NVDA", submitted_limit=0.50, fill_price=0.55, realized_pnl=None, closed_at=None),  # still open
    ]
    d = reflector.digest([_row("NO_REGIME")], trades)
    assert d.closed_trades == 2
    assert d.wins == 1
    assert d.realized_pnl == pytest.approx(-180.0)
    assert d.worst_trade == ("TSLA", -300.0)


def test_digest_slippage_scoped_to_filled_trades_regardless_of_closed_status() -> None:
    """avg_slippage_vs_mid is an entry-time fact (fill_price - submitted_limit,
    signed) -- it must include the still-open NVDA fill above, unlike the
    realized_pnl fields, and must skip a trade that never filled at all."""
    trades = [
        _trade("SPY", submitted_limit=-0.90, fill_price=-0.85, realized_pnl=120.0, closed_at="t1"),  # +0.05
        _trade("NVDA", submitted_limit=0.50, fill_price=0.55, realized_pnl=None, closed_at=None),     # +0.05
        _trade("ORCL", submitted_limit=-0.40, fill_price=None, realized_pnl=None, closed_at=None),    # UNFILLED_REJECT, excluded
    ]
    d = reflector.digest([_row("NO_REGIME")], trades)
    assert d.avg_slippage_vs_mid == pytest.approx(0.05)


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


async def test_reflect_calls_llm_when_all_denylisted_but_a_trade_closed() -> None:
    """docs/review.md Task 7: the trap the brief calls out by name -- adding
    P&L to the digest/prompt does nothing if reflect() still returns before
    the LLM call. When every observed gate reason is denylisted BUT a trade
    closed this session, there is something to reflect on, so the widened
    gate (`binding_constraint is None and closed_trades == 0`) must let this
    one through instead of short-circuiting."""
    rows = [_row("DEGENERATE_CHAIN"), _row("DEGENERATE_CHAIN")]
    trades = [_trade("LLY", submitted_limit=1.94, fill_price=6.65, realized_pnl=-671.0, closed_at="t1")]
    d = reflector.digest(rows, trades)
    assert d.binding_constraint is None
    assert d.closed_trades == 1

    llm = ScriptedLlm(_OUTPUT)
    result = await reflector.reflect(llm, d, sink=[])
    assert result.ok is True
    assert llm.calls == ["REFLECTOR"]
    # The prompt must actually say something was closed and lost money --
    # not just "Binding constraint: None" -- and must still name the
    # guardrail as off-limits so the model can't route around REFLECTOR_DENYLIST.
    prompt = llm.prompts[0]
    assert "none eligible" in prompt
    assert "closed trade" in prompt
    assert "-671.00" in prompt


def test_prompt_includes_outcome_line_only_when_a_trade_closed() -> None:
    """The outcome block must be silent (no 'Realized outcome' line) on an
    ordinary pre-Thursday session with zero closed trades, so the prompt
    doesn't claim outcome grounding it doesn't have."""
    d_no_trades = reflector.digest([_row("NO_REGIME")])
    assert "Realized outcome" not in reflector._prompt(d_no_trades)

    d_with_trade = reflector.digest(
        [_row("NO_REGIME")],
        [_trade("SPY", submitted_limit=-0.90, fill_price=-0.85, realized_pnl=50.0, closed_at="t1")],
    )
    prompt = reflector._prompt(d_with_trade)
    assert "Realized outcome" in prompt
    assert "1 win(s)" in prompt


class ScriptedLlm:
    def __init__(self, result: ReflectorOutput | Exception) -> None:
        self._result = result
        self.calls: list[str] = []
        self.prompts: list[str] = []

    async def complete_json(self, prompt, schema, *, node, system=None, sink=None):
        self.calls.append(node)
        self.prompts.append(prompt)
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

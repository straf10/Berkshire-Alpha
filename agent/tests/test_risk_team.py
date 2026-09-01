from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import pytest

from agent.agents.evidence import EvidenceBundle
from agent.agents.risk_team import run_risk_team
from agent.risk.gates import GateContext, evaluate
from agent.schemas.execution import Intent, Leg, Regime, SpreadPlan, Structure
from agent.schemas.llm import RiskManagerOutput
from agent.schemas.market import QuantSnapshot
from agent.strategy.macro import MacroRegime, MacroSnapshot
from agent.strategy.regime import RegimeDecision
from agent.tools.llm import LlmUnavailable, LlmValidationDropped

_MACRO = MacroSnapshot(
    regime=MacroRegime.NEUTRAL, gold_z=0.0, oil_z=0.0, btc_z=0.0,
    bars_used=65, horizon="SLOW", detail="test fixture",
)

EXPIRY = date(2026, 9, 4)
SESSION_DATE = date(2026, 8, 31)


@dataclass
class FakeAccount:
    equity: Decimal
    last_equity: Decimal
    buying_power: Decimal


@dataclass
class FakePortfolio:
    delta_dollars: float
    vega_dollars: float
    delta_limit: float
    vega_limit: float
    position_keys: frozenset


def _account(equity: Decimal = Decimal("100000")) -> FakeAccount:
    return FakeAccount(equity=equity, last_equity=equity, buying_power=equity)


def _portfolio(equity: Decimal = Decimal("100000")) -> FakePortfolio:
    return FakePortfolio(
        delta_dollars=0.0, vega_dollars=0.0,
        delta_limit=0.15 * float(equity), vega_limit=0.02 * float(equity),
        position_keys=frozenset(),
    )


def _leg(side: str, strike: float, delta: float) -> Leg:
    intent = Intent.SELL_TO_OPEN if side == "SELL" else Intent.BUY_TO_OPEN
    occ = f"TST260904P{int(strike * 1000):08d}"
    return Leg(occ_symbol=occ, strike=strike, right="P", side=side, ratio_qty=1, intent=intent,
               delta=delta, vega=0.05, bid=1.0, ask=1.1)


def _plan(*, max_loss: Decimal = Decimal("210"), max_profit: Decimal = Decimal("90")) -> SpreadPlan:
    return SpreadPlan(
        symbol="TST", structure=Structure.BULL_PUT_SPREAD, regime=Regime.CREDIT, expiry=EXPIRY, dte=4,
        legs=(_leg("SELL", 100.0, -0.28), _leg("BUY", 97.0, -0.10)), width=3.0,
        net_mid=Decimal("-0.90"), net_natural=Decimal("-0.75"),
        max_profit_per_spread=max_profit, max_loss_per_spread=max_loss,
        p_success=0.72, spot=100.0, short_leg_delta=0.28,
    )


def _bundle() -> EvidenceBundle:
    quant = QuantSnapshot(
        symbol="TST", session_date=SESSION_DATE, spot=100.0, rv_20=0.20, iv_atm=0.25,
        vrp_ratio=1.4, skew_abs=6.0, vwap=100.0, vwap_dev_pct=0.4, rsi=63.2, vwm=0.0,
        vwm_z=1.6, target_expiry=EXPIRY, dte=4, data_ok=True, drop_reason=None,
    )
    regime = RegimeDecision(Regime.CREDIT, Structure.BULL_PUT_SPREAD, "test", "TEST", None, None)
    return EvidenceBundle(
        symbol="TST", quant=quant, regime=regime, macro=_MACRO, quant_analyst=None, news_analyst=None,
        sentiment_analyst=None, headlines=(), mentions=None,
    )


def _gate_ctx(**overrides) -> GateContext:
    from agent.risk.greeks import PortfolioGreeks

    portfolio = PortfolioGreeks(
        delta_dollars=0.0, vega_dollars=0.0, delta_limit=15000.0, vega_limit=2000.0,
        delta_breached=False, vega_breached=False, largest_delta_contributor=None,
        largest_vega_contributor=None, position_keys=frozenset(),
    )
    base = dict(
        equity=Decimal("100000"), buying_power=Decimal("50000"), day_pnl_pct=0.0, drawdown_pct=0.0,
        open_position_keys=frozenset(), open_underlyings=frozenset(), aggregate_defined_risk=Decimal("0"),
        portfolio=portfolio, session_date=SESSION_DATE, past_entry_cutoff=False, reduce_only=False,
        chain_symbols=frozenset({"TST260904P00100000", "TST260904P00097000"}), earnings_armed=False,
    )
    base.update(overrides)
    return GateContext(**base)


def _vote(persona: str, decision: str) -> RiskManagerOutput:
    return RiskManagerOutput(
        persona=persona, decision=decision, max_loss_acceptable=True,
        risk_reward_ratio_acceptable=True, manager_notes="x",
    )


class ScriptedLlm:
    """Returns one queued RiskManagerOutput (or raises a queued exception) per
    node, in call order. Distinct calls are matched by node name."""

    def __init__(self, by_node: dict[str, object]) -> None:
        self._by_node = dict(by_node)
        self.calls = 0

    async def complete_json(self, prompt, schema, *, node, system=None, sink=None):
        self.calls += 1
        item = self._by_node.get(node)
        if isinstance(item, Exception):
            raise item
        return item


async def test_two_rejects_veto() -> None:
    llm = ScriptedLlm({
        "RISK_AGGRESSIVE": _vote("AGGRESSIVE", "APPROVE"),
        "RISK_NEUTRAL": _vote("NEUTRAL", "REJECT"),
        "RISK_CONSERVATIVE": _vote("CONSERVATIVE", "REJECT"),
    })
    result = await run_risk_team(llm, _plan(), _bundle(), _account(), _portfolio(), sem=asyncio.Semaphore(3), sink=[])
    assert result.vetoed
    assert result.veto_reason == "RISK_TEAM_VETO"


async def test_one_reject_does_not_veto() -> None:
    llm = ScriptedLlm({
        "RISK_AGGRESSIVE": _vote("AGGRESSIVE", "APPROVE"),
        "RISK_NEUTRAL": _vote("NEUTRAL", "APPROVE"),
        "RISK_CONSERVATIVE": _vote("CONSERVATIVE", "REJECT"),
    })
    result = await run_risk_team(llm, _plan(), _bundle(), _account(), _portfolio(), sem=asyncio.Semaphore(3), sink=[])
    assert not result.vetoed
    assert len(result.votes) == 3


async def test_resize_vote_does_not_change_qty() -> None:
    """All three RESIZE: run_risk_team records the votes, and qty is decided
    entirely by risk.sizing/gates.evaluate -- the same qty as if no risk team
    had run at all. The baseline is computed BEFORE run_risk_team runs, so
    this actually proves the votes had no effect on the plan or the gate,
    rather than just proving evaluate() is deterministic when called twice
    on an unmodified plan."""
    plan = _plan()
    baseline = evaluate(plan, _gate_ctx())

    llm = ScriptedLlm({
        "RISK_AGGRESSIVE": _vote("AGGRESSIVE", "RESIZE"),
        "RISK_NEUTRAL": _vote("NEUTRAL", "RESIZE"),
        "RISK_CONSERVATIVE": _vote("CONSERVATIVE", "RESIZE"),
    })
    result = await run_risk_team(llm, plan, _bundle(), _account(), _portfolio(), sem=asyncio.Semaphore(3), sink=[])
    assert not result.vetoed
    assert all(v.decision == "RESIZE" for v in result.votes)

    after_votes = evaluate(plan, _gate_ctx())
    assert after_votes.approved == baseline.approved
    assert after_votes.qty == baseline.qty


async def test_dropped_persona_absent_not_fatal() -> None:
    llm = ScriptedLlm({
        "RISK_AGGRESSIVE": _vote("AGGRESSIVE", "APPROVE"),
        "RISK_NEUTRAL": LlmValidationDropped("bad json twice"),
        "RISK_CONSERVATIVE": _vote("CONSERVATIVE", "APPROVE"),
    })
    result = await run_risk_team(llm, _plan(), _bundle(), _account(), _portfolio(), sem=asyncio.Semaphore(3), sink=[])
    assert not result.vetoed
    assert len(result.votes) == 2


async def test_all_personas_dropped_no_veto_no_exception() -> None:
    llm = ScriptedLlm({
        "RISK_AGGRESSIVE": LlmUnavailable("timeout"),
        "RISK_NEUTRAL": LlmUnavailable("timeout"),
        "RISK_CONSERVATIVE": LlmUnavailable("timeout"),
    })
    result = await run_risk_team(llm, _plan(), _bundle(), _account(), _portfolio(), sem=asyncio.Semaphore(3), sink=[])
    assert not result.vetoed
    assert result.votes == ()


async def test_personas_run_concurrently() -> None:
    class ConcurrencyLlm:
        def __init__(self) -> None:
            self._concurrent = 0
            self.max_concurrent = 0

        async def complete_json(self, prompt, schema, *, node, system=None, sink=None):
            self._concurrent += 1
            self.max_concurrent = max(self.max_concurrent, self._concurrent)
            await asyncio.sleep(0.01)
            self._concurrent -= 1
            return _vote(node.removeprefix("RISK_"), "APPROVE")

    llm = ConcurrencyLlm()
    await run_risk_team(llm, _plan(), _bundle(), _account(), _portfolio(), sem=asyncio.Semaphore(3), sink=[])
    assert llm.max_concurrent == 3

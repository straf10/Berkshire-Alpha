from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from agent.agents.evidence import EvidenceBundle
from agent.agents.researchers import DebateResult, Verdict
from agent.agents.trader import (
    ProposalFailure,
    ProposalOutcome,
    _infer_structure,
    propose,
    strike_table,
    validate_proposal,
)
from agent.schemas.execution import Regime, SpreadPlan, Structure
from agent.schemas.llm import DebateNodeOutput, OptionLegProposal, SpreadProposal
from agent.schemas.market import ChainSnapshot, OptionQuote, QuantSnapshot
from agent.strategy.regime import RegimeDecision
from agent.strategy.spread_builder import build, build_from_proposal
from agent.tests.fixture_helpers import load_chain_raw
from agent.tools import market_data

SESSION_DATE = date(2026, 8, 31)
EXPIRY = date(2026, 9, 4)
_TS = datetime(2026, 8, 28, tzinfo=timezone.utc)

# Aug-31 anchor: Sept-4 (4 DTE) valid, Sept-2 (2 DTE, too soon) also a listed
# trading day, Sept-5 (Saturday) deliberately absent.
TRADING_DAYS = frozenset({date(2026, 8, 31), date(2026, 9, 2), date(2026, 9, 3), date(2026, 9, 4)})


def _quote(strike: float, right, delta: float, bid: float, ask: float, symbol: str = "TST") -> OptionQuote:
    return OptionQuote(
        occ_symbol=f"{symbol}{EXPIRY:%y%m%d}{right}{int(strike * 1000):08d}",
        underlying=symbol, expiry=EXPIRY, strike=strike, right=right,
        bid=bid, ask=ask, delta=delta, gamma=0.01, theta=-0.01, vega=0.05, iv=0.2,
    )


def _chain(contracts) -> ChainSnapshot:
    return ChainSnapshot(underlying="TST", fetched_at=_TS, contracts=tuple(contracts))


# Same $3-wide put grid as test_spread_builder.py: short lands on 100 (only
# delta in the (0.22, 0.33) band), long falls back one increment to 97.
_PUT_CREDIT_CHAIN = _chain([
    _quote(91.0, "P", delta=-0.05, bid=0.02, ask=0.04),
    _quote(94.0, "P", delta=-0.12, bid=0.05, ask=0.08),
    _quote(97.0, "P", delta=-0.20, bid=0.10, ask=0.20),
    _quote(100.0, "P", delta=-0.275, bid=1.00, ask=1.10),
    _quote(103.0, "P", delta=-0.45, bid=2.50, ask=2.60),
])


def _snapshot(**overrides) -> QuantSnapshot:
    base = dict(
        symbol="TST", session_date=SESSION_DATE, spot=100.0, rv_20=0.15, iv_atm=0.20,
        vrp_ratio=1.4, skew_abs=6.0, vwap=100.0, vwap_dev_pct=0.0, rsi=50.0, vwm=0.0,
        vwm_z=0.0, target_expiry=EXPIRY, dte=4, data_ok=True, drop_reason=None,
    )
    base.update(overrides)
    return QuantSnapshot(**base)


def _decision(structure: Structure, regime: Regime = Regime.CREDIT) -> RegimeDecision:
    return RegimeDecision(regime, structure, "test", "TEST", None, None)


def _bundle(q: QuantSnapshot, d: RegimeDecision) -> EvidenceBundle:
    return EvidenceBundle(
        symbol=q.symbol, quant=q, regime=d, quant_analyst=None, news_analyst=None,
        sentiment_analyst=None, headlines=(), mentions=None,
    )


def _bull_put_proposal(sell_strike: float = 100.0, buy_strike: float = 97.0, expiry: str = "2026-09-04") -> SpreadProposal:
    return SpreadProposal(
        underlying="TST", strategy_name="bull put spread", expiration_date=expiry,
        legs=[
            OptionLegProposal(contract_type="PUT", side="SELL", strike_price=sell_strike, ratio_qty=1),
            OptionLegProposal(contract_type="PUT", side="BUY", strike_price=buy_strike, ratio_qty=1),
        ],
        confidence_score=0.8, reasoning="grid says so",
    )


def _debate_result() -> DebateResult:
    node = DebateNodeOutput(agent_persona="BULL", doc_action="COMMIT", evidence_cited=[], volatility_view="v", rebuttal_argument="r")
    return DebateResult(nodes=(node,), rounds_run=1, consensus_score=0.9, verdict=Verdict.CONSENSUS_ROUND_1, terminated_early=True, conviction=1.0)


class FakeLlm:
    def __init__(self) -> None:
        self.node_scripts: dict[str, list] = {}
        self.calls = 0

    def script(self, node: str, items: list) -> None:
        self.node_scripts[node] = list(items)

    async def complete_json(self, prompt, schema, *, node, system=None, sink=None):
        self.calls += 1
        items = self.node_scripts.get(node)
        if not items:
            raise AssertionError(f"no scripted response left for node {node}")
        item = items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


async def test_hallucinated_strike_one_retry_then_deterministic_fallback() -> None:
    """Two bad proposals still buy exactly one retry -- but the candidate is no
    longer dropped: spread_builder.build() picks the strikes, and the outcome
    says so. Day-1 post-mortem (2026-08-31): a formatting failure here was the
    single reason the agent traded nothing all session."""
    llm = FakeLlm()
    bad = _bull_put_proposal(sell_strike=999.0, buy_strike=97.0)
    llm.script("TRADER", [bad, bad])
    q, d = _snapshot(), _decision(Structure.BULL_PUT_SPREAD)
    result = await propose(llm, _bundle(q, d), _debate_result(), _PUT_CREDIT_CHAIN, TRADING_DAYS, sink=[])
    assert llm.calls == 2
    assert isinstance(result, ProposalOutcome)
    assert result.proposal is None                                   # never a fabricated model response
    assert result.fallback_from == ProposalFailure.STRIKE_NOT_IN_CHAIN
    assert result.plan == build(q, d, _PUT_CREDIT_CHAIN)             # identical to the quant-only pick


async def test_fallback_drops_candidate_when_builder_also_declines() -> None:
    """An untradeable chain still ends in a real no-trade -- the fallback is a
    second opinion, not a guarantee of a trade."""
    llm = FakeLlm()
    bad = _bull_put_proposal(sell_strike=999.0, buy_strike=97.0)
    llm.script("TRADER", [bad, bad])
    # No delta inside SHORT_DELTA_BAND, so build() returns
    # NO_SHORT_STRIKE_IN_DELTA_BAND and there is nothing to fall back to.
    chain = _chain([
        _quote(97.0, "P", delta=-0.02, bid=0.05, ask=0.08),
        _quote(100.0, "P", delta=-0.05, bid=0.10, ask=0.20),
    ])
    q, d = _snapshot(), _decision(Structure.BULL_PUT_SPREAD)
    result = await propose(llm, _bundle(q, d), _debate_result(), chain, TRADING_DAYS, sink=[])
    assert result == ProposalFailure.STRIKE_NOT_IN_CHAIN


def test_infer_structure_covers_all_four_verticals() -> None:
    """Regression, 2026-08-31: the call side of the ordering table was
    inverted, so a correctly-formed BEAR_CALL_SPREAD read as a
    BULL_CALL_SPREAD and every call-credit proposal failed validation as
    STRUCTURE_MISMATCH no matter what the model proposed. Four of the six
    candidates that reached the trader on Day 1 were bear call spreads."""
    def legs(a_side, a_strike, b_side, b_strike, right):
        return (
            OptionLegProposal(contract_type=right, side=a_side, strike_price=a_strike, ratio_qty=1),
            OptionLegProposal(contract_type=right, side=b_side, strike_price=b_strike, ratio_qty=1),
        )
    assert _infer_structure(legs("SELL", 95.0, "BUY", 100.0, "CALL")) == Structure.BEAR_CALL_SPREAD
    assert _infer_structure(legs("BUY", 95.0, "SELL", 100.0, "CALL")) == Structure.BULL_CALL_SPREAD
    assert _infer_structure(legs("SELL", 100.0, "BUY", 97.0, "PUT")) == Structure.BULL_PUT_SPREAD
    assert _infer_structure(legs("BUY", 100.0, "SELL", 97.0, "PUT")) == Structure.BEAR_PUT_SPREAD


def test_bear_call_spread_proposal_validates() -> None:
    """The end-to-end shape of the Day-1 blocker: TSLA/META were both assigned
    BEAR_CALL_SPREAD and both failed here, twice each, in both scans."""
    q, d = _snapshot(), _decision(Structure.BEAR_CALL_SPREAD)
    chain = _chain([
        _quote(100.0, "C", delta=0.45, bid=2.50, ask=2.60),
        _quote(103.0, "C", delta=0.275, bid=1.00, ask=1.10),
        _quote(106.0, "C", delta=0.15, bid=0.30, ask=0.40),
    ])
    p = SpreadProposal(
        underlying="TST", strategy_name="bear call spread", expiration_date="2026-09-04",
        legs=[
            OptionLegProposal(contract_type="CALL", side="SELL", strike_price=103.0, ratio_qty=1),
            OptionLegProposal(contract_type="CALL", side="BUY", strike_price=106.0, ratio_qty=1),
        ],
        confidence_score=0.8, reasoning="sell the over-bid call",
    )
    assert validate_proposal(p, q, d, chain, TRADING_DAYS) is None


def test_proposal_expiry_out_of_window() -> None:
    q, d = _snapshot(), _decision(Structure.BULL_PUT_SPREAD)
    p = _bull_put_proposal(expiry="2026-09-02")
    assert validate_proposal(p, q, d, _PUT_CREDIT_CHAIN, TRADING_DAYS) == ProposalFailure.EXPIRY_NOT_IN_WINDOW


def test_proposal_expiry_not_a_trading_day() -> None:
    q, d = _snapshot(), _decision(Structure.BULL_PUT_SPREAD)
    p = _bull_put_proposal(expiry="2026-09-05")
    assert validate_proposal(p, q, d, _PUT_CREDIT_CHAIN, TRADING_DAYS) == ProposalFailure.EXPIRY_NOT_TRADING_DAY


def test_proposal_structure_mismatch() -> None:
    q, d = _snapshot(), _decision(Structure.BULL_PUT_SPREAD)  # bullish/credit regime
    p = SpreadProposal(
        underlying="TST", strategy_name="bear call spread", expiration_date="2026-09-04",
        legs=[
            OptionLegProposal(contract_type="CALL", side="SELL", strike_price=95.0, ratio_qty=1),
            OptionLegProposal(contract_type="CALL", side="BUY", strike_price=100.0, ratio_qty=1),
        ],
        confidence_score=0.8, reasoning="wrong side",
    )
    chain = _chain([
        _quote(95.0, "C", delta=0.45, bid=6.00, ask=6.20),
        _quote(100.0, "C", delta=0.275, bid=2.50, ask=2.60),
    ])
    assert validate_proposal(p, q, d, chain, TRADING_DAYS) == ProposalFailure.STRUCTURE_MISMATCH


def test_plan_prices_ignore_llm() -> None:
    q, d = _snapshot(), _decision(Structure.BULL_PUT_SPREAD)
    proposal = _bull_put_proposal()
    plan_direct = build(q, d, _PUT_CREDIT_CHAIN)
    plan_from_proposal = build_from_proposal(q, d, _PUT_CREDIT_CHAIN, proposal)
    assert isinstance(plan_direct, SpreadPlan)
    assert isinstance(plan_from_proposal, SpreadPlan)
    assert plan_from_proposal.net_mid == plan_direct.net_mid
    assert plan_from_proposal.net_natural == plan_direct.net_natural
    assert plan_from_proposal.max_loss_per_spread == plan_direct.max_loss_per_spread
    assert plan_from_proposal.max_profit_per_spread == plan_direct.max_profit_per_spread


def test_confidence_never_reaches_sizing() -> None:
    for rel in ("agent/agents/trader.py", "agent/strategy/spread_builder.py"):
        src = Path(rel).read_text(encoding="utf-8")
        assert "confidence_score" not in src, f"{rel}: confidence_score must never enter sizing"


def test_strike_table_bounded() -> None:
    raw = load_chain_raw("chain_SPY.json")
    chain = market_data._build_chain_snapshot("SPY", raw)
    assert chain is not None
    rows = strike_table(chain, EXPIRY, "P", spot=772.0)
    assert len(rows) <= 24
    assert all(set(r.keys()) == {"strike", "bid", "ask", "delta"} for r in rows)


async def test_unanimous_disagree_still_reaches_trader() -> None:
    from agent.agents.researchers import run_debate
    from agent.config import CONVICTION_UNANIMOUS_DISAGREE_FLOOR

    class DisagreeLlm(FakeLlm):
        pass

    llm = DisagreeLlm()
    # 2026-08-31 pre-market unblock: unanimous DISAGREE terminates at round 1
    # (2 calls, not 4) with conviction floored to
    # CONVICTION_UNANIMOUS_DISAGREE_FLOOR rather than 0.0 -- it is no longer
    # an absolute veto, so pipeline.py now always calls propose() regardless
    # of the debate's own verdict label (still UNRESOLVED here).
    llm.script("DEBATE_BULL", [
        DebateNodeOutput(agent_persona="BULL", doc_action="DISAGREE", evidence_cited=[], volatility_view="v", rebuttal_argument="r"),
    ])
    llm.script("DEBATE_BEAR", [
        DebateNodeOutput(agent_persona="BEAR", doc_action="DISAGREE", evidence_cited=[], volatility_view="v", rebuttal_argument="r"),
    ])
    llm.script("TRADER", [_bull_put_proposal()])
    q, d = _snapshot(), _decision(Structure.BULL_PUT_SPREAD)
    bundle = _bundle(q, d)
    debate = await run_debate(llm, bundle, sink=[])
    assert debate.verdict == Verdict.UNRESOLVED
    assert debate.conviction == pytest.approx(CONVICTION_UNANIMOUS_DISAGREE_FLOOR)

    result = await propose(llm, bundle, debate, _PUT_CREDIT_CHAIN, TRADING_DAYS, sink=[])
    assert not isinstance(result, ProposalFailure)

    assert llm.calls == 3

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from agent.agents.pipeline import run_llm_pipeline
from agent.config import UNIVERSE
from agent.schemas.execution import Regime, Structure
from agent.schemas.llm import (
    DebateNodeOutput,
    NewsAnalystOutput,
    OptionLegProposal,
    QuantAnalystOutput,
    RiskManagerOutput,
    SentimentAnalystOutput,
    SpreadProposal,
)
from agent.schemas.market import ChainSnapshot, OptionQuote, QuantSnapshot
from agent.strategy.regime import RegimeDecision
from agent.strategy.ticker_screener import ScreenedCandidate
from agent.tools.llm import LlmBudgetExceeded
from agent.tools.news import Headline
from agent.tools.reddit import MentionSignal, RedditPost

SESSION_DATE = date(2026, 8, 31)
EXPIRY = date(2026, 9, 4)
TRADING_DAYS = frozenset({date(2026, 8, 31), date(2026, 9, 2), date(2026, 9, 3), date(2026, 9, 4)})
_TS = datetime(2026, 8, 28, tzinfo=timezone.utc)


def _snapshot(symbol: str) -> QuantSnapshot:
    return QuantSnapshot(
        symbol=symbol, session_date=SESSION_DATE, spot=100.0, rv_20=0.15, iv_atm=0.20,
        vrp_ratio=1.4, skew_abs=6.0, vwap=100.0, vwap_dev_pct=0.0, rsi=50.0, vwm=0.0,
        vwm_z=0.0, target_expiry=EXPIRY, dte=4, data_ok=True, drop_reason=None,
    )


def _candidate(symbol: str, score: float = 0.5) -> ScreenedCandidate:
    decision = RegimeDecision(Regime.CREDIT, Structure.BULL_PUT_SPREAD, "test", "TEST", None, None)
    return ScreenedCandidate(snapshot=_snapshot(symbol), decision=decision, score=score)


def _quote(strike: float, delta: float, bid: float, ask: float, symbol: str) -> OptionQuote:
    return OptionQuote(
        occ_symbol=f"{symbol}{EXPIRY:%y%m%d}P{int(strike * 1000):08d}",
        underlying=symbol, expiry=EXPIRY, strike=strike, right="P",
        bid=bid, ask=ask, delta=delta, gamma=0.01, theta=-0.01, vega=0.05, iv=0.2,
    )


def _put_credit_chain(symbol: str) -> ChainSnapshot:
    contracts = [
        _quote(91.0, -0.05, 0.02, 0.04, symbol), _quote(94.0, -0.12, 0.05, 0.08, symbol),
        _quote(97.0, -0.20, 0.10, 0.20, symbol), _quote(100.0, -0.275, 1.00, 1.10, symbol),
        _quote(103.0, -0.45, 2.50, 2.60, symbol),
    ]
    return ChainSnapshot(underlying=symbol, fetched_at=_TS, contracts=tuple(contracts))


class FakeChains:
    def __init__(self, chains: dict[str, ChainSnapshot]) -> None:
        self._chains = chains

    def get(self, symbol: str) -> ChainSnapshot | None:
        return self._chains.get(symbol)


@dataclass
class FakeAccount:
    equity: Decimal = Decimal("100000")
    last_equity: Decimal = Decimal("100000")
    buying_power: Decimal = Decimal("100000")


@dataclass
class FakePortfolio:
    delta_dollars: float = 0.0
    vega_dollars: float = 0.0
    delta_limit: float = 15000.0
    vega_limit: float = 2000.0
    position_keys: frozenset = frozenset()


def _quant_out(symbol: str) -> QuantAnalystOutput:
    return QuantAnalystOutput(ticker=symbol, iv_rv_interpretation="RICH", skew_bias="BULLISH",
                               directional_momentum="WEAK_UP", key_levels=[100.0], analyst_summary="s")


def _news_out(symbol: str) -> NewsAnalystOutput:
    return NewsAnalystOutput(ticker=symbol, catalyst_summary="none", expected_impact="NEUTRAL",
                              impact_horizon_days=3, headline_ids_cited=[], analyst_summary="s")


def _sentiment_out(symbol: str) -> SentimentAnalystOutput:
    return SentimentAnalystOutput(ticker=symbol, sentiment_score=0.0, confidence=0.5,
                                   mention_velocity_read="NORMAL", top_themes=[], analyst_summary="s")


def _debate_node(persona: str, action: str, keys: list[str]) -> DebateNodeOutput:
    return DebateNodeOutput(agent_persona=persona, doc_action=action, evidence_cited=keys,
                             volatility_view="v", rebuttal_argument="r")


def _proposal() -> SpreadProposal:
    return SpreadProposal(
        underlying="SYM", strategy_name="bull put spread", expiration_date="2026-09-04",
        legs=[
            OptionLegProposal(contract_type="PUT", side="SELL", strike_price=100.0, ratio_qty=1),
            OptionLegProposal(contract_type="PUT", side="BUY", strike_price=97.0, ratio_qty=1),
        ],
        confidence_score=0.8, reasoning="grid says so",
    )


def _risk_vote(persona: str, decision: str = "APPROVE") -> RiskManagerOutput:
    return RiskManagerOutput(persona=persona, decision=decision, max_loss_acceptable=True,
                              risk_reward_ratio_acceptable=True, manager_notes="x")


def _extract_symbol(prompt: str) -> str:
    first_line = prompt.split("\n")[0]
    for marker in ("Underlying:", "Ticker:"):
        if marker in first_line:
            after = first_line.split(marker, 1)[1].strip()
            return after.split()[0] if after else "SYM"
    return "SYM"


class ScriptedLlm:
    """FakeLlm keyed by node, with a per-node queue; falls back to a
    plausible default so consensus is reached and a valid proposal is built
    unless a test explicitly overrides a node."""

    def __init__(self) -> None:
        self.node_scripts: dict[str, list] = {}
        self.calls: list[str] = []

    def script(self, node: str, items: list) -> None:
        self.node_scripts[node] = list(items)

    async def complete_json(self, prompt, schema, *, node, system=None, sink=None):
        self.calls.append(node)
        items = self.node_scripts.get(node)
        if items:
            item = items.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        symbol = _extract_symbol(prompt)
        if node == "QUANT":
            return _quant_out(symbol)
        if node == "NEWS":
            return _news_out(symbol)
        if node == "SENTIMENT":
            return _sentiment_out(symbol)
        if node == "DEBATE_BULL":
            return _debate_node("BULL", "COMMIT", ["quant.vrp_ratio", "regime.structure"])
        if node == "DEBATE_BEAR":
            return _debate_node("BEAR", "COMMIT", ["quant.vrp_ratio", "regime.structure"])
        if node == "TRADER":
            proposal = _proposal()
            return proposal.model_copy(update={"underlying": symbol})
        return _risk_vote(node.removeprefix("RISK_"))


async def test_not_top_candidates_get_not_top_debate_outcome() -> None:
    """4 shortlisted, only DEBATE_CANDIDATES=2 survive to debate -- every
    candidate still gets a PipelineOutcome, per Day 2's 'no_trade is a
    first-class decision' (docs/day3_llm_plan.md Group 5 property 4)."""
    llm = ScriptedLlm()
    candidates = [_candidate(UNIVERSE[i], score=float(i)) for i in range(4)]
    chains = FakeChains({c.snapshot.symbol: _put_credit_chain(c.snapshot.symbol) for c in candidates})
    sinks = {c.snapshot.symbol: [] for c in candidates}

    outcomes = await run_llm_pipeline(
        llm, candidates, chains, {}, {}, FakeAccount(), FakePortfolio(), TRADING_DAYS,
        sem=asyncio.Semaphore(6), sinks=sinks,
    )
    assert len(outcomes) == 4
    not_top = [o for o in outcomes if o.reason == "NOT_TOP_DEBATE_CANDIDATE"]
    assert len(not_top) == 2
    for o in not_top:
        assert o.plan is None
        assert o.artifacts.debate_nodes == ()


async def test_debate_unanimous_disagree_stops_before_trader() -> None:
    """docs/day4_track_ab_plan.md §2.3: unanimous DISAGREE terminates at round
    1 (conviction 0.0) and is the only remaining debate-driven no-trade."""
    llm = ScriptedLlm()
    llm.script("DEBATE_BULL", [_debate_node("BULL", "DISAGREE", [])])
    llm.script("DEBATE_BEAR", [_debate_node("BEAR", "DISAGREE", [])])
    candidates = [_candidate(UNIVERSE[0])]
    chains = FakeChains({UNIVERSE[0]: _put_credit_chain(UNIVERSE[0])})
    sinks = {UNIVERSE[0]: []}

    outcomes = await run_llm_pipeline(
        llm, candidates, chains, {}, {}, FakeAccount(), FakePortfolio(), TRADING_DAYS,
        sem=asyncio.Semaphore(6), sinks=sinks,
    )
    assert len(outcomes) == 1
    assert outcomes[0].reason == "DEBATE_UNANIMOUS_DISAGREE"
    assert outcomes[0].plan is None
    assert outcomes[0].conviction == 0.0
    assert "TRADER" not in llm.calls
    assert not any(c.startswith("RISK_") for c in llm.calls)


async def test_pipeline_no_unresolved_drop() -> None:
    """docs/day4_track_ab_plan.md §2.3: a split debate that never reaches
    CONSENSUS_HIGH_THRESHOLD (old-style UNRESOLVED) no longer drops the
    candidate -- it proceeds to a plan with reduced (but nonzero) conviction."""
    llm = ScriptedLlm()
    cites = ["quant.vrp_ratio", "regime.structure"]
    llm.script("DEBATE_BULL", [_debate_node("BULL", "COMMIT", cites), _debate_node("BULL", "COMMIT", cites)])
    llm.script("DEBATE_BEAR", [_debate_node("BEAR", "DISAGREE", []), _debate_node("BEAR", "DISAGREE", [])])
    candidates = [_candidate(UNIVERSE[0])]
    chains = FakeChains({UNIVERSE[0]: _put_credit_chain(UNIVERSE[0])})
    sinks = {UNIVERSE[0]: []}

    outcomes = await run_llm_pipeline(
        llm, candidates, chains, {}, {}, FakeAccount(), FakePortfolio(), TRADING_DAYS,
        sem=asyncio.Semaphore(6), sinks=sinks,
    )
    outcome = outcomes[0]
    assert outcome.artifacts.debate_summary.verdict == "UNRESOLVED"
    assert 0.0 < outcome.conviction < 1.0
    assert outcome.plan is not None
    assert outcome.reason == "OK"


async def test_risk_veto_stops_before_gate() -> None:
    llm = ScriptedLlm()
    llm.script("RISK_AGGRESSIVE", [_risk_vote("AGGRESSIVE", "REJECT")])
    llm.script("RISK_NEUTRAL", [_risk_vote("NEUTRAL", "REJECT")])
    candidates = [_candidate(UNIVERSE[0])]
    chains = FakeChains({UNIVERSE[0]: _put_credit_chain(UNIVERSE[0])})
    sinks = {UNIVERSE[0]: []}

    outcomes = await run_llm_pipeline(
        llm, candidates, chains, {}, {}, FakeAccount(), FakePortfolio(), TRADING_DAYS,
        sem=asyncio.Semaphore(6), sinks=sinks,
    )
    assert outcomes[0].reason == "RISK_TEAM_VETO"
    assert outcomes[0].plan is None
    assert outcomes[0].artifacts.proposal_row is not None
    assert outcomes[0].artifacts.proposal_row.accepted is False


async def test_full_happy_path_produces_plan() -> None:
    llm = ScriptedLlm()
    candidates = [_candidate(UNIVERSE[0])]
    chains = FakeChains({UNIVERSE[0]: _put_credit_chain(UNIVERSE[0])})
    sinks = {UNIVERSE[0]: []}

    outcomes = await run_llm_pipeline(
        llm, candidates, chains, {}, {}, FakeAccount(), FakePortfolio(), TRADING_DAYS,
        sem=asyncio.Semaphore(6), sinks=sinks,
    )
    outcome = outcomes[0]
    assert outcome.reason == "OK"
    assert outcome.plan is not None
    assert outcome.mode == "llm"
    assert outcome.artifacts.proposal_row.accepted is True
    assert len(outcome.artifacts.risk_rows) == 3


async def test_mode_degraded_when_analyst_dropped() -> None:
    """news_analyst only calls the LLM when there are headlines to read (a
    token-efficiency fix -- an empty headline set never reaches the LLM at
    all, so this test needs a real headline to exercise the drop path)."""
    from agent.tools.llm import LlmValidationDropped

    llm = ScriptedLlm()
    llm.script("NEWS", [LlmValidationDropped("bad")])
    candidates = [_candidate(UNIVERSE[0])]
    chains = FakeChains({UNIVERSE[0]: _put_credit_chain(UNIVERSE[0])})
    sinks = {UNIVERSE[0]: []}
    headline = Headline.build(
        id="n1", symbol=UNIVERSE[0], headline="h", source="s", created_at=_TS, summary="s",
    )
    news = {UNIVERSE[0]: (headline,)}

    outcomes = await run_llm_pipeline(
        llm, candidates, chains, news, {}, FakeAccount(), FakePortfolio(), TRADING_DAYS,
        sem=asyncio.Semaphore(6), sinks=sinks,
    )
    assert outcomes[0].mode == "llm-degraded"


async def test_budget_exceeded_propagates_out_of_pipeline() -> None:
    """LlmBudgetExceeded must escape run_llm_pipeline entirely -- it is the
    caller's (scan_cycle's) job to catch it and degrade the whole cycle to
    quant-only, not the pipeline's (docs/day3_llm_plan.md Group 5 property 3)."""
    llm = ScriptedLlm()
    llm.script("QUANT", [LlmBudgetExceeded("ceiling hit")])
    candidates = [_candidate(UNIVERSE[0])]
    chains = FakeChains({UNIVERSE[0]: _put_credit_chain(UNIVERSE[0])})
    sinks = {UNIVERSE[0]: []}

    with pytest.raises(LlmBudgetExceeded):
        await run_llm_pipeline(
            llm, candidates, chains, {}, {}, FakeAccount(), FakePortfolio(), TRADING_DAYS,
            sem=asyncio.Semaphore(6), sinks=sinks,
        )


async def test_full_cycle_call_count() -> None:
    """docs/day3_llm_plan.md Group 5: 4 shortlisted, 2 debated, both
    terminating at R1 -> exactly 12 (analysts) + 4 (R1 debate) + 2 (trader)
    + 6 (risk) = 24 FakeLlm calls. Every candidate needs a real headline and
    mention signal so all 12 analyst calls actually fire (news_analyst and
    sentiment_analyst skip the call entirely with no input)."""
    llm = ScriptedLlm()
    candidates = [_candidate(UNIVERSE[i], score=float(i)) for i in range(4)]
    symbols = [c.snapshot.symbol for c in candidates]
    chains = FakeChains({s: _put_credit_chain(s) for s in symbols})
    sinks = {s: [] for s in symbols}
    news = {
        s: (Headline.build(id=f"n{i}", symbol=s, headline="h", source="s", created_at=_TS, summary="s"),)
        for i, s in enumerate(symbols)
    }
    post = RedditPost(id="p1", subreddit="stocks", title="t", created_utc=_TS, score=1, num_comments=0)
    mentions = {s: MentionSignal(symbol=s, mentions=1, baseline=1.0, velocity=1.0, posts=(post,)) for s in symbols}

    outcomes = await run_llm_pipeline(
        llm, candidates, chains, news, mentions, FakeAccount(), FakePortfolio(), TRADING_DAYS,
        sem=asyncio.Semaphore(6), sinks=sinks,
    )
    assert len(outcomes) == 4
    assert sum(1 for o in outcomes if o.reason == "OK") == 2
    assert len(llm.calls) == 24

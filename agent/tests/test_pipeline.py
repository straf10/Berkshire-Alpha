from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from agent.agents.pipeline import run_llm_pipeline
from agent.config import ANALYST_SCORE_FLOOR, CONVICTION_UNANIMOUS_DISAGREE_FLOOR, UNIVERSE
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
from agent.strategy.macro import MacroRegime, MacroSnapshot
from agent.strategy.regime import RegimeDecision
from agent.strategy.ticker_screener import ScreenedCandidate
from agent.tools.llm import LlmBudgetExceeded
from agent.tools.news import Headline
from agent.tools.reddit import MentionSignal, RedditPost

SESSION_DATE = date(2026, 8, 31)
EXPIRY = date(2026, 9, 4)
TRADING_DAYS = frozenset({date(2026, 8, 31), date(2026, 9, 2), date(2026, 9, 3), date(2026, 9, 4)})
_TS = datetime(2026, 8, 28, tzinfo=timezone.utc)

_MACRO = MacroSnapshot(
    regime=MacroRegime.NEUTRAL, gold_z=0.0, oil_z=0.0, btc_z=0.0,
    bars_used=65, horizon="SLOW", detail="test fixture",
)


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
    """6 shortlisted, only DEBATE_CANDIDATES=4 survive to debate -- every
    candidate still gets a PipelineOutcome, per Day 2's 'no_trade is a
    first-class decision' (docs/day3_llm_plan.md Group 5 property 4)."""
    llm = ScriptedLlm()
    candidates = [_candidate(UNIVERSE[i], score=float(i)) for i in range(6)]
    chains = FakeChains({c.snapshot.symbol: _put_credit_chain(c.snapshot.symbol) for c in candidates})
    sinks = {c.snapshot.symbol: [] for c in candidates}

    outcomes = await run_llm_pipeline(
        llm, candidates, chains, {}, {}, FakeAccount(), FakePortfolio(), TRADING_DAYS,
        sem=asyncio.Semaphore(6), sinks=sinks, macro=_MACRO,
    )
    assert len(outcomes) == 6
    not_top = [o for o in outcomes if o.reason == "NOT_TOP_DEBATE_CANDIDATE"]
    assert len(not_top) == 2
    for o in not_top:
        assert o.plan is None
        assert o.artifacts.debate_nodes == ()


def _contrary_quant_out(symbol: str) -> QuantAnalystOutput:
    """quant_component == 0 against the default _candidate's BULL_PUT_SPREAD/
    CREDIT structure (direction=+1): STRONG_DOWN contradicts on momentum,
    CHEAP contradicts on IV for a credit structure."""
    return QuantAnalystOutput(ticker=symbol, iv_rv_interpretation="CHEAP", skew_bias="BEARISH",
                               directional_momentum="STRONG_DOWN", key_levels=[100.0], analyst_summary="s")


async def test_floor_reject_costs_no_debate_calls() -> None:
    """docs/day4_action_plan.md §8.2b/§8.4: a candidate whose analysts
    contradict the deterministic structure on both momentum and IV scores
    below ANALYST_SCORE_FLOOR and must be vetoed before the debate -- exactly
    the 2 analyst calls run, no DEBATE_*/TRADER/RISK_* node ever fires."""
    llm = ScriptedLlm()
    llm.script("QUANT", [_contrary_quant_out(UNIVERSE[0])])
    llm.script("NEWS", [_news_out(UNIVERSE[0])])
    candidates = [_candidate(UNIVERSE[0])]
    chains = FakeChains({UNIVERSE[0]: _put_credit_chain(UNIVERSE[0])})
    sinks = {UNIVERSE[0]: []}
    headline = Headline.build(id="n1", symbol=UNIVERSE[0], headline="h", source="s", created_at=_TS, summary="s")
    news = {UNIVERSE[0]: (headline,)}

    outcomes = await run_llm_pipeline(
        llm, candidates, chains, news, {}, FakeAccount(), FakePortfolio(), TRADING_DAYS,
        sem=asyncio.Semaphore(6), sinks=sinks, macro=_MACRO,
    )
    assert outcomes[0].reason == "ANALYST_SCORE_BELOW_FLOOR"
    assert outcomes[0].analyst_score < ANALYST_SCORE_FLOOR
    assert set(llm.calls) == {"QUANT", "NEWS"}
    assert len(llm.calls) == 2


async def test_floor_reject_still_writes_decision() -> None:
    """A floor-rejected candidate still produces a PipelineOutcome (the
    precursor to a decisions row, per Day 2's 'no_trade is a first-class
    decision') rather than being silently dropped from the returned list."""
    llm = ScriptedLlm()
    llm.script("QUANT", [_contrary_quant_out(UNIVERSE[0])])
    candidates = [_candidate(UNIVERSE[0])]
    chains = FakeChains({UNIVERSE[0]: _put_credit_chain(UNIVERSE[0])})
    sinks = {UNIVERSE[0]: []}

    outcomes = await run_llm_pipeline(
        llm, candidates, chains, {}, {}, FakeAccount(), FakePortfolio(), TRADING_DAYS,
        sem=asyncio.Semaphore(6), sinks=sinks, macro=_MACRO,
    )
    assert len(outcomes) == 1
    outcome = outcomes[0]
    assert outcome.reason == "ANALYST_SCORE_BELOW_FLOOR"
    assert outcome.plan is None
    assert outcome.artifacts.analyst_rows


async def test_debate_unanimous_disagree_floors_conviction_but_still_trades() -> None:
    """2026-08-31 pre-market unblock: unanimous DISAGREE no longer stops the
    candidate before the trader -- conviction floors to
    CONVICTION_UNANIMOUS_DISAGREE_FLOOR and the candidate still reaches
    proposal -> risk team -> the deterministic gate (which is the only layer
    left that can still reject on conviction, as LOW_CONVICTION)."""
    llm = ScriptedLlm()
    llm.script("DEBATE_BULL", [_debate_node("BULL", "DISAGREE", [])])
    llm.script("DEBATE_BEAR", [_debate_node("BEAR", "DISAGREE", [])])
    candidates = [_candidate(UNIVERSE[0])]
    chains = FakeChains({UNIVERSE[0]: _put_credit_chain(UNIVERSE[0])})
    sinks = {UNIVERSE[0]: []}

    outcomes = await run_llm_pipeline(
        llm, candidates, chains, {}, {}, FakeAccount(), FakePortfolio(), TRADING_DAYS,
        sem=asyncio.Semaphore(6), sinks=sinks, macro=_MACRO,
    )
    assert len(outcomes) == 1
    assert outcomes[0].reason == "OK"
    assert outcomes[0].plan is not None
    assert outcomes[0].conviction == pytest.approx(CONVICTION_UNANIMOUS_DISAGREE_FLOOR)
    assert "TRADER" in llm.calls
    assert any(c.startswith("RISK_") for c in llm.calls)


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
        sem=asyncio.Semaphore(6), sinks=sinks, macro=_MACRO,
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
        sem=asyncio.Semaphore(6), sinks=sinks, macro=_MACRO,
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
        sem=asyncio.Semaphore(6), sinks=sinks, macro=_MACRO,
    )
    outcome = outcomes[0]
    assert outcome.reason == "OK"
    assert outcome.plan is not None
    assert outcome.mode == "llm"
    assert outcome.artifacts.proposal_row.accepted is True
    assert len(outcome.artifacts.risk_rows) == 3


async def test_trader_failure_falls_back_to_deterministic_strikes() -> None:
    """Day-1 post-mortem (2026-08-31): 6/6 debated candidates died at
    STRUCTURE_MISMATCH and the session produced no trades. A trader model that
    cannot format two strikes now hands strike selection to
    spread_builder.build() and the candidate continues to the risk team and the
    gate, recorded as mode='llm-fallback' with the model's failure kept in the
    proposal row."""
    llm = ScriptedLlm()
    hallucinated = _proposal().model_copy(update={"underlying": UNIVERSE[0], "legs": [
        OptionLegProposal(contract_type="PUT", side="SELL", strike_price=999.0, ratio_qty=1),
        OptionLegProposal(contract_type="PUT", side="BUY", strike_price=997.0, ratio_qty=1),
    ]})
    llm.script("TRADER", [hallucinated, hallucinated])
    candidates = [_candidate(UNIVERSE[0])]
    chains = FakeChains({UNIVERSE[0]: _put_credit_chain(UNIVERSE[0])})
    sinks = {UNIVERSE[0]: []}

    outcomes = await run_llm_pipeline(
        llm, candidates, chains, {}, {}, FakeAccount(), FakePortfolio(), TRADING_DAYS,
        sem=asyncio.Semaphore(6), sinks=sinks, macro=_MACRO,
    )
    outcome = outcomes[0]
    assert outcome.reason == "OK"
    assert outcome.plan is not None
    assert outcome.mode == "llm-fallback"
    assert llm.calls.count("TRADER") == 2                 # the single retry is still the budget
    assert len(outcome.artifacts.risk_rows) == 3          # risk team still votes
    row = json.loads(outcome.artifacts.proposal_row.proposal_json)
    assert row["source"] == "spread_builder.build"
    assert row["fallback_from"] == "STRIKE_NOT_IN_CHAIN"
    assert {leg["strike_price"] for leg in row["legs"]} == {100.0, 97.0}


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
        sem=asyncio.Semaphore(6), sinks=sinks, macro=_MACRO,
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
            sem=asyncio.Semaphore(6), sinks=sinks, macro=_MACRO,
        )


async def test_full_cycle_call_count() -> None:
    """docs/day3_llm_plan.md Group 5, DEBATE_CANDIDATES=4, updated for
    docs/day4_action_plan.md Step 1 (sentiment_analyst retired from
    run_analysts): 4 shortlisted, all 4 debated, all terminating at R1 ->
    exactly 8 (QUANT+NEWS analysts) + 8 (R1 debate) + 4 (trader) + 12 (risk)
    = 32 FakeLlm calls. Every candidate needs a real headline so all 8
    analyst calls actually fire (news_analyst skips the call entirely with
    no input); `mentions` is passed through but no longer drives any call."""
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
        sem=asyncio.Semaphore(6), sinks=sinks, macro=_MACRO,
    )
    assert len(outcomes) == 4
    assert sum(1 for o in outcomes if o.reason == "OK") == 4
    assert len(llm.calls) == 32

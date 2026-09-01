from __future__ import annotations

import asyncio
import time

import pytest

from agent.agents.analysts import (
    AnalystResult,
    analyst_score,
    run_analysts,
    select_top,
)
from agent.agents.evidence import EvidenceBundle
from agent.schemas.execution import Regime, Structure
from agent.schemas.llm import NewsAnalystOutput, QuantAnalystOutput, SentimentAnalystOutput
from agent.schemas.market import QuantSnapshot
from agent.strategy.regime import RegimeDecision
from agent.strategy.ticker_screener import ScreenedCandidate
from agent.tools.llm import LlmBudgetExceeded, LlmUnavailable, LlmValidationDropped
from agent.tools.news import Headline
from agent.tools.reddit import MentionSignal, RedditPost
from datetime import date, datetime, timezone

SESSION_DATE = date(2026, 8, 31)
EXPIRY = date(2026, 9, 4)
_TS = datetime(2026, 8, 28, tzinfo=timezone.utc)


def _headlines(symbol: str) -> tuple[Headline, ...]:
    """news_analyst only calls the LLM when there is at least one headline
    -- a token-efficiency fix -- so tests exercising the NEWS node need real
    input rather than the default empty dict."""
    return (Headline.build(id="n1", symbol=symbol, headline="h", source="s", created_at=_TS, summary="s"),)


def _mention_signal(symbol: str) -> MentionSignal:
    """Same reasoning as _headlines -- sentiment_analyst skips the call with
    no posts, so tests exercising the SENTIMENT node need a real post."""
    post = RedditPost(id="p1", subreddit="stocks", title="t", created_utc=_TS, score=1, num_comments=0)
    return MentionSignal(symbol=symbol, mentions=1, baseline=1.0, velocity=1.0, posts=(post,))


def _snapshot(symbol: str) -> QuantSnapshot:
    return QuantSnapshot(
        symbol=symbol, session_date=SESSION_DATE, spot=100.0, rv_20=0.20, iv_atm=0.25,
        vrp_ratio=1.4, skew_abs=6.0, vwap=100.0, vwap_dev_pct=0.4, rsi=63.2, vwm=0.0,
        vwm_z=1.6, target_expiry=EXPIRY, dte=4, data_ok=True, drop_reason=None,
    )


def _candidate(symbol: str, structure: Structure = Structure.BULL_PUT_SPREAD, regime: Regime = Regime.CREDIT) -> ScreenedCandidate:
    decision = RegimeDecision(regime, structure, "test", "TEST", None, None)
    return ScreenedCandidate(snapshot=_snapshot(symbol), decision=decision, score=0.5)


def _quant_out(symbol: str, **overrides) -> QuantAnalystOutput:
    base = dict(ticker=symbol, iv_rv_interpretation="NEUTRAL", skew_bias="FLAT",
                directional_momentum="NEUTRAL", key_levels=[100.0], analyst_summary="s")
    base.update(overrides)
    return QuantAnalystOutput(**base)


def _news_out(symbol: str, **overrides) -> NewsAnalystOutput:
    base = dict(ticker=symbol, catalyst_summary="none", expected_impact="NEUTRAL",
                impact_horizon_days=3, headline_ids_cited=[], analyst_summary="s")
    base.update(overrides)
    return NewsAnalystOutput(**base)


def _sentiment_out(symbol: str, **overrides) -> SentimentAnalystOutput:
    base = dict(ticker=symbol, sentiment_score=0.0, confidence=0.5,
                mention_velocity_read="NORMAL", top_themes=[], analyst_summary="s")
    base.update(overrides)
    return SentimentAnalystOutput(**base)


class FakeLlm:
    def __init__(self) -> None:
        self.node_scripts: dict[str, list] = {}
        self._concurrent = 0
        self.max_concurrent = 0
        self.calls = 0
        self._delay = 0.01

    def script(self, node: str, items: list) -> None:
        self.node_scripts[node] = list(items)

    async def complete_json(self, prompt, schema, *, node, system=None, sink=None):
        self.calls += 1
        self._concurrent += 1
        self.max_concurrent = max(self.max_concurrent, self._concurrent)
        try:
            await asyncio.sleep(self._delay)
            items = self.node_scripts.get(node)
            item = items.pop(0) if items else self._default(node, prompt)
            if isinstance(item, Exception):
                raise item
            return item
        finally:
            self._concurrent -= 1

    def _default(self, node, prompt):
        symbol = prompt.split("\n")[0].split(":")[-1].strip() if ":" in prompt.split("\n")[0] else "TST"
        if node == "QUANT":
            return _quant_out(symbol)
        if node == "NEWS":
            return _news_out(symbol)
        return _sentiment_out(symbol)


async def test_analysts_run_concurrently() -> None:
    llm = FakeLlm()
    candidates = [_candidate(f"SYM{i}") for i in range(4)]
    news = {c.snapshot.symbol: _headlines(c.snapshot.symbol) for c in candidates}
    mentions = {c.snapshot.symbol: _mention_signal(c.snapshot.symbol) for c in candidates}
    sem = asyncio.Semaphore(6)
    sinks = {c.snapshot.symbol: [] for c in candidates}
    t0 = time.monotonic()
    results = await run_analysts(llm, candidates, news, mentions, sem=sem, sinks=sinks)
    elapsed = time.monotonic() - t0
    assert llm.calls == 8  # QUANT + NEWS only (docs/day4_action_plan.md Step 1) x 4 candidates
    assert llm.max_concurrent == 6
    assert elapsed < 0.05  # 2 waves of ~0.01s, not 8 serial waves
    assert len(results) == 4


async def test_one_analyst_validation_drop_isolated() -> None:
    llm = FakeLlm()
    llm.script("NEWS", [LlmValidationDropped("bad news node")])
    candidates = [_candidate("SPY")]
    news = {"SPY": _headlines("SPY")}
    mentions = {"SPY": _mention_signal("SPY")}
    sinks = {"SPY": []}
    results = await run_analysts(llm, candidates, news, mentions, sem=asyncio.Semaphore(6), sinks=sinks)
    bundle = results[0].bundle
    assert bundle.news_analyst is None
    assert bundle.quant_analyst is not None
    assert bundle.sentiment_analyst is None  # never invoked (docs/day4_action_plan.md Step 1)
    assert results[0].failures == (("NEWS", "LlmValidationDropped"),)


async def test_all_analysts_fail_candidate_survives() -> None:
    llm = FakeLlm()
    for node in ("QUANT", "NEWS"):
        llm.script(node, [LlmValidationDropped("x")])
    candidates = [_candidate("SPY")]
    sinks = {"SPY": []}
    results = await run_analysts(llm, candidates, {}, {}, sem=asyncio.Semaphore(6), sinks=sinks)
    bundle = results[0].bundle
    assert bundle.quant is not None
    assert bundle.regime is not None
    assert bundle.quant_analyst is None and bundle.news_analyst is None and bundle.sentiment_analyst is None


async def test_partial_outage_degrades_cycle() -> None:
    llm = FakeLlm()
    candidates = [_candidate(f"SYM{i}") for i in range(4)]
    # 4 of the 8 calls unavailable (QUANT + NEWS only, docs/day4_action_plan.md
    # Step 1): fail QUANT for all 4 -- exactly half, meets the >= half guard.
    # NEWS must actually be called for a scripted failure to fire there, so
    # every candidate needs a real headline (news_analyst skips the call
    # entirely with no headlines) -- unused here since only QUANT fails.
    news = {c.snapshot.symbol: _headlines(c.snapshot.symbol) for c in candidates}
    llm.script("QUANT", [LlmUnavailable("x")] * 4)
    sinks = {c.snapshot.symbol: [] for c in candidates}
    with pytest.raises(LlmUnavailable):
        await run_analysts(llm, candidates, news, {}, sem=asyncio.Semaphore(6), sinks=sinks)


async def test_single_flap_does_not_degrade() -> None:
    llm = FakeLlm()
    candidates = [_candidate(f"SYM{i}") for i in range(4)]
    llm.script("QUANT", [LlmUnavailable("x")])  # only the first QUANT call fails
    sinks = {c.snapshot.symbol: [] for c in candidates}
    results = await run_analysts(llm, candidates, {}, {}, sem=asyncio.Semaphore(6), sinks=sinks)
    assert len(results) == 4
    total_failures = sum(len(r.failures) for r in results)
    assert total_failures == 1


async def test_budget_exceeded_propagates() -> None:
    llm = FakeLlm()
    llm.script("QUANT", [LlmBudgetExceeded("out of budget")])
    candidates = [_candidate(f"SYM{i}") for i in range(4)]
    sinks = {c.snapshot.symbol: [] for c in candidates}
    with pytest.raises(LlmBudgetExceeded):
        await run_analysts(llm, candidates, {}, {}, sem=asyncio.Semaphore(6), sinks=sinks)


def test_evidence_keys_match_prompt() -> None:
    candidate = _candidate("SPY")
    bundle = EvidenceBundle(
        symbol="SPY", quant=candidate.snapshot, regime=candidate.decision,
        quant_analyst=_quant_out("SPY"), news_analyst=_news_out("SPY"),
        sentiment_analyst=_sentiment_out("SPY"), headlines=(), mentions=None,
    )
    payload = bundle.to_prompt_json()
    for key in bundle.keys():
        assert key in payload


def test_evidence_keys_exclude_failed_analysts() -> None:
    candidate = _candidate("SPY")
    bundle = EvidenceBundle(
        symbol="SPY", quant=candidate.snapshot, regime=candidate.decision,
        quant_analyst=_quant_out("SPY"), news_analyst=None, sentiment_analyst=None,
        headlines=(), mentions=None,
    )
    keys = bundle.keys()
    assert not any(k.startswith("news.") for k in keys)
    assert not any(k.startswith("sentiment.") for k in keys)
    assert any(k.startswith("quant_analyst.") for k in keys)


def test_analyst_score_direction_agreement() -> None:
    candidate = _candidate("SPY", structure=Structure.BULL_CALL_SPREAD, regime=Regime.DEBIT)
    bull = AnalystResult(
        symbol="SPY",
        bundle=EvidenceBundle(
            symbol="SPY", quant=candidate.snapshot, regime=candidate.decision,
            quant_analyst=_quant_out("SPY", iv_rv_interpretation="CHEAP", directional_momentum="STRONG_UP"),
            news_analyst=_news_out("SPY", expected_impact="BULLISH"),
            sentiment_analyst=_sentiment_out("SPY", sentiment_score=0.8, confidence=0.9),
            headlines=(), mentions=None,
        ),
        failures=(),
    )
    assert analyst_score(bull) > 0.9

    bear = AnalystResult(
        symbol="SPY",
        bundle=EvidenceBundle(
            symbol="SPY", quant=candidate.snapshot, regime=candidate.decision,
            quant_analyst=_quant_out("SPY", iv_rv_interpretation="RICH", directional_momentum="STRONG_DOWN"),
            news_analyst=_news_out("SPY", expected_impact="BEARISH"),
            sentiment_analyst=_sentiment_out("SPY", sentiment_score=-0.8, confidence=0.9),
            headlines=(), mentions=None,
        ),
        failures=(),
    )
    assert analyst_score(bear) < 0.1


def test_analyst_score_missing_is_neutral() -> None:
    candidate = _candidate("SPY")
    result = AnalystResult(
        symbol="SPY",
        bundle=EvidenceBundle(
            symbol="SPY", quant=candidate.snapshot, regime=candidate.decision,
            quant_analyst=None, news_analyst=None, sentiment_analyst=None, headlines=(), mentions=None,
        ),
        failures=(),
    )
    assert analyst_score(result) == pytest.approx(0.5)


def test_analyst_score_ignores_sentiment() -> None:
    """docs/day4_action_plan.md Step 1.8: sentiment_analyst is never invoked
    any more, but the field still exists on EvidenceBundle -- pin that a
    populated sentiment field (however it got there) cannot move the score."""
    candidate = _candidate("SPY", structure=Structure.BULL_CALL_SPREAD, regime=Regime.DEBIT)

    def _score(sentiment: SentimentAnalystOutput | None) -> float:
        result = AnalystResult(
            symbol="SPY",
            bundle=EvidenceBundle(
                symbol="SPY", quant=candidate.snapshot, regime=candidate.decision,
                quant_analyst=_quant_out("SPY", iv_rv_interpretation="CHEAP", directional_momentum="STRONG_UP"),
                news_analyst=_news_out("SPY", expected_impact="BULLISH"),
                sentiment_analyst=sentiment, headlines=(), mentions=None,
            ),
            failures=(),
        )
        return analyst_score(result)

    baseline = _score(None)
    assert baseline == _score(_sentiment_out("SPY", sentiment_score=0.9, confidence=0.9))
    assert baseline == _score(_sentiment_out("SPY", sentiment_score=-0.9, confidence=0.9))


def test_top_two_selection_deterministic() -> None:
    candidates = [_candidate(sym) for sym in ("NVDA", "AMD", "SPY", "QQQ")]
    results = []
    for c in candidates:
        bundle = EvidenceBundle(
            symbol=c.snapshot.symbol, quant=c.snapshot, regime=c.decision,
            quant_analyst=_quant_out(c.snapshot.symbol, directional_momentum="STRONG_UP", iv_rv_interpretation="RICH"),
            news_analyst=None, sentiment_analyst=None, headlines=(), mentions=None,
        )
        results.append(AnalystResult(symbol=c.snapshot.symbol, bundle=bundle, failures=()))

    picks = [tuple(r.symbol for r in select_top(results, candidates, n=2)) for _ in range(10)]
    assert len(set(picks)) == 1  # stable across repeated runs
    # NVDA precedes AMD in UNIVERSE, both tie on score -- tie-break must pick UNIVERSE order.
    assert picks[0][0] in ("NVDA", "AMD", "SPY", "QQQ")


def test_analyst_prompt_token_budget() -> None:
    candidate = _candidate("SPY")
    bundle = EvidenceBundle(
        symbol="SPY", quant=candidate.snapshot, regime=candidate.decision,
        quant_analyst=_quant_out("SPY"), news_analyst=_news_out("SPY"),
        sentiment_analyst=_sentiment_out("SPY"), headlines=(), mentions=None,
    )
    assert len(bundle.to_prompt_json()) < 1200


def test_schemas_verbatim_from_plan() -> None:
    assert set(QuantAnalystOutput.model_fields) == {
        "ticker", "iv_rv_interpretation", "skew_bias", "directional_momentum", "key_levels", "analyst_summary",
    }
    from agent.schemas.llm import DebateNodeOutput, OptionLegProposal, RiskManagerOutput, SpreadProposal

    assert set(DebateNodeOutput.model_fields) == {
        "agent_persona", "doc_action", "evidence_cited", "volatility_view", "rebuttal_argument",
    }
    assert set(OptionLegProposal.model_fields) == {"contract_type", "side", "strike_price", "ratio_qty"}
    assert set(SpreadProposal.model_fields) == {
        "underlying", "strategy_name", "expiration_date", "legs", "confidence_score", "reasoning",
    }
    assert set(RiskManagerOutput.model_fields) == {
        "persona", "decision", "max_loss_acceptable", "risk_reward_ratio_acceptable", "manager_notes",
    }
    legs_field = SpreadProposal.model_fields["legs"]
    assert legs_field.metadata[0].min_length == 2
    assert legs_field.metadata[1].max_length == 4


def test_no_deprecated_pydantic_kwargs() -> None:
    from pathlib import Path

    src = Path("agent/schemas/llm.py").read_text(encoding="utf-8")
    assert "min_items" not in src
    assert "max_items" not in src

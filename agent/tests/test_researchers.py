from __future__ import annotations

from datetime import date

import pytest

from agent.agents.evidence import EvidenceBundle
from agent.agents.researchers import (
    DebateResult,
    Verdict,
    consensus_score,
    run_debate,
    valid_citations,
)
from agent.config import CONSENSUS_HIGH_THRESHOLD
from agent.schemas.execution import Regime, Structure
from agent.schemas.llm import DebateNodeOutput
from agent.schemas.market import QuantSnapshot
from agent.strategy.regime import RegimeDecision
from agent.tools.llm import LlmUnavailable

SESSION_DATE = date(2026, 8, 31)
EXPIRY = date(2026, 9, 4)


def _snapshot() -> QuantSnapshot:
    return QuantSnapshot(
        symbol="SPY", session_date=SESSION_DATE, spot=100.0, rv_20=0.20, iv_atm=0.25,
        vrp_ratio=1.4, skew_abs=6.0, vwap=100.0, vwap_dev_pct=0.4, rsi=63.2, vwm=0.0,
        vwm_z=1.6, target_expiry=EXPIRY, dte=4, data_ok=True, drop_reason=None,
    )


def _bundle() -> EvidenceBundle:
    decision = RegimeDecision(Regime.CREDIT, Structure.BULL_PUT_SPREAD, "test", "TEST", None, None)
    return EvidenceBundle(
        symbol="SPY", quant=_snapshot(), regime=decision,
        quant_analyst=None, news_analyst=None, sentiment_analyst=None,
        headlines=(), mentions=None,
    )


def _node(persona: str, action: str, cites: list[str]) -> DebateNodeOutput:
    return DebateNodeOutput(
        agent_persona=persona, doc_action=action, evidence_cited=cites,
        volatility_view="v", rebuttal_argument="r",
    )


class FakeLlm:
    def __init__(self) -> None:
        self.node_scripts: dict[str, list] = {}
        self.calls = 0
        self.call_nodes: list[str] = []

    def script(self, node: str, items: list) -> None:
        self.node_scripts[node] = list(items)

    async def complete_json(self, prompt, schema, *, node, system=None, sink=None):
        self.calls += 1
        self.call_nodes.append(node)
        items = self.node_scripts.get(node)
        if not items:
            raise AssertionError(f"no scripted response left for node {node}")
        item = items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


async def test_sprt_terminates_round_1() -> None:
    llm = FakeLlm()
    cites = ["quant.vrp_ratio", "quant.skew_abs", "quant.rsi"]
    llm.script("DEBATE_BULL", [_node("BULL", "COMMIT", cites)])
    llm.script("DEBATE_BEAR", [_node("BEAR", "COMMIT", cites)])
    result = await run_debate(llm, _bundle(), sink=[])
    assert result.rounds_run == 1
    assert result.terminated_early
    assert result.verdict == Verdict.CONSENSUS_ROUND_1
    assert llm.calls == 2


async def test_contested_runs_round_2() -> None:
    llm = FakeLlm()
    cites = ["quant.vrp_ratio", "quant.skew_abs", "quant.rsi"]
    llm.script("DEBATE_BULL", [_node("BULL", "COMMIT", cites), _node("BULL", "COMMIT", cites)])
    llm.script("DEBATE_BEAR", [_node("BEAR", "DISAGREE", []), _node("BEAR", "COMMIT", cites)])
    result = await run_debate(llm, _bundle(), sink=[])
    assert result.rounds_run == 2
    assert llm.calls == 4


async def test_ungrounded_agreement_does_not_terminate() -> None:
    llm = FakeLlm()
    bull1 = _node("BULL", "COMMIT", ["quant.vrp_ratio"])
    bear1 = _node("BEAR", "COMMIT", ["quant.skew_abs"])
    assert consensus_score(bull1, bear1, _bundle().keys()) == pytest.approx(0.8)
    cites = ["quant.vrp_ratio", "quant.skew_abs", "quant.rsi"]
    llm.script("DEBATE_BULL", [bull1, _node("BULL", "COMMIT", cites)])
    llm.script("DEBATE_BEAR", [bear1, _node("BEAR", "COMMIT", cites)])
    result = await run_debate(llm, _bundle(), sink=[])
    assert result.rounds_run == 2
    assert not result.terminated_early


def test_fabricated_citations_score_zero() -> None:
    keys = _bundle().keys()
    node = _node("BULL", "COMMIT", ["vrp is 9.9", "insider tip"])
    assert valid_citations(node, keys) == 0


async def test_hard_cap_two_rounds() -> None:
    llm = FakeLlm()
    llm.script("DEBATE_BULL", [_node("BULL", "DISAGREE", []), _node("BULL", "DISAGREE", [])])
    llm.script("DEBATE_BEAR", [_node("BEAR", "DISAGREE", []), _node("BEAR", "DISAGREE", [])])
    result = await run_debate(llm, _bundle(), sink=[])
    assert result.rounds_run == 2
    assert llm.calls == 4
    assert result.verdict == Verdict.UNRESOLVED


async def test_missing_node_counts_as_disagree() -> None:
    llm = FakeLlm()
    cites = ["quant.vrp_ratio", "quant.skew_abs", "quant.rsi"]
    llm.script("DEBATE_BULL", [_node("BULL", "COMMIT", cites), _node("BULL", "COMMIT", cites)])
    llm.script("DEBATE_BEAR", [LlmUnavailable("x"), LlmUnavailable("x")])
    result = await run_debate(llm, _bundle(), sink=[])
    assert result.consensus_score <= 0.65
    assert not result.terminated_early
    assert result.rounds_run == 2
    assert result.verdict == Verdict.UNRESOLVED


def test_consensus_score_boundary() -> None:
    keys = _bundle().keys()
    cites3 = ["quant.vrp_ratio", "quant.skew_abs", "quant.rsi"]
    cites2 = ["quant.vrp_ratio", "quant.skew_abs"]
    cites1 = ["quant.vrp_ratio"]

    strong = consensus_score(_node("BULL", "COMMIT", cites2), _node("BEAR", "COMMIT", cites2), keys)
    assert strong >= CONSENSUS_HIGH_THRESHOLD

    weak = consensus_score(_node("BULL", "COMMIT", cites1), _node("BEAR", "COMMIT", cites1), keys)
    assert weak <= 0.84

    contested = consensus_score(_node("BULL", "COMMIT", cites3), _node("BEAR", "DISAGREE", cites3), keys)
    assert contested <= 0.65

    floor = consensus_score(_node("BULL", "DISAGREE", []), _node("BEAR", "DISAGREE", []), keys)
    assert floor == 0.0

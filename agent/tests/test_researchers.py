from __future__ import annotations

from datetime import date

import pytest

from agent.agents.evidence import EvidenceBundle
from agent.agents.researchers import (
    DebateResult,
    Verdict,
    consensus_score,
    conviction,
    run_debate,
    valid_citations,
)
from agent.config import CONSENSUS_HIGH_THRESHOLD, CONVICTION_UNANIMOUS_DISAGREE_FLOOR
from agent.schemas.execution import Regime, Structure
from agent.schemas.llm import DebateNodeOutput
from agent.schemas.market import QuantSnapshot
from agent.strategy.macro import MacroRegime, MacroSnapshot
from agent.strategy.regime import RegimeDecision
from agent.tools.llm import LlmUnavailable

SESSION_DATE = date(2026, 8, 31)
EXPIRY = date(2026, 9, 4)

_MACRO = MacroSnapshot(
    regime=MacroRegime.NEUTRAL, gold_z=0.0, oil_z=0.0, btc_z=0.0,
    bars_used=65, horizon="SLOW", detail="test fixture",
)


def _snapshot() -> QuantSnapshot:
    return QuantSnapshot(
        symbol="SPY", session_date=SESSION_DATE, spot=100.0, rv_20=0.20, iv_atm=0.25,
        vrp_ratio=1.4, skew_abs=6.0, vwap=100.0, vwap_dev_pct=0.4, rsi=63.2, vwm=0.0,
        vwm_z=1.6, target_expiry=EXPIRY, dte=4, data_ok=True, drop_reason=None,
    )


def _bundle(*, macro: MacroSnapshot = _MACRO) -> EvidenceBundle:
    decision = RegimeDecision(Regime.CREDIT, Structure.BULL_PUT_SPREAD, "test", "TEST", None, None)
    return EvidenceBundle(
        symbol="SPY", quant=_snapshot(), regime=decision, macro=macro,
        quant_analyst=None, news_analyst=None, sentiment_analyst=None,
        headlines=(), mentions=None,
    )


def test_evidence_keys_include_macro() -> None:
    assert "macro.regime" in _bundle().keys()


def test_prompt_json_contains_macro() -> None:
    """The invariant that citations are checkable: 'macro.regime' must be a
    literal substring of to_prompt_json(), the same way every other citation
    key is."""
    assert '"macro.regime"' in _bundle().to_prompt_json()


def test_macro_citation_counts_as_grounded() -> None:
    """A debate node citing only macro.regime must not be treated as
    ungrounded by valid_citations/conviction -- macro.regime is a first-class
    citable key like any quant.* key."""
    bundle = _bundle()
    node = _node("BULL", "COMMIT", ["macro.regime"])
    assert valid_citations(node, bundle.keys()) == 1


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


async def test_ungrounded_unanimous_commit_terminates_with_haircut() -> None:
    """docs/day4_track_ab_plan.md §2.2/D6 regression: unanimous COMMIT with
    thin citations used to force a pointless round 2 (chasing a consensus_score
    that citation count alone could never clear); now it terminates at round 1
    like any other unanimous outcome, with conviction absorbing the haircut."""
    llm = FakeLlm()
    bull1 = _node("BULL", "COMMIT", ["quant.vrp_ratio"])
    bear1 = _node("BEAR", "COMMIT", ["quant.skew_abs"])
    assert consensus_score(bull1, bear1, _bundle().keys()) == pytest.approx(0.8)
    llm.script("DEBATE_BULL", [bull1])
    llm.script("DEBATE_BEAR", [bear1])
    result = await run_debate(llm, _bundle(), sink=[])
    assert result.rounds_run == 1
    assert result.terminated_early
    assert result.verdict == Verdict.CONSENSUS_ROUND_1
    assert 0.0 < result.conviction < 1.0


def test_fabricated_citations_score_zero() -> None:
    keys = _bundle().keys()
    node = _node("BULL", "COMMIT", ["vrp is 9.9", "insider tip"])
    assert valid_citations(node, keys) == 0


async def test_hard_cap_two_rounds() -> None:
    """A split debate (not unanimous either way) always runs exactly 2 rounds
    and never more, regardless of round 2's outcome -- DEBATE_MAX_ROUNDS=2 is
    a hard ceiling, not merely a default."""
    llm = FakeLlm()
    cites = ["quant.vrp_ratio", "quant.skew_abs", "quant.rsi"]
    llm.script("DEBATE_BULL", [_node("BULL", "COMMIT", cites), _node("BULL", "COMMIT", cites)])
    llm.script("DEBATE_BEAR", [_node("BEAR", "DISAGREE", []), _node("BEAR", "DISAGREE", [])])
    result = await run_debate(llm, _bundle(), sink=[])
    assert result.rounds_run == 2
    assert llm.calls == 4
    assert result.verdict == Verdict.UNRESOLVED


async def test_unanimous_disagree_terminates_round_1_at_floor_conviction() -> None:
    """2026-08-31 pre-market unblock: unanimous DISAGREE still terminates at
    round 1, but conviction floors to CONVICTION_UNANIMOUS_DISAGREE_FLOOR
    instead of 0.0 -- it is a size floor, not an absolute veto."""
    llm = FakeLlm()
    llm.script("DEBATE_BULL", [_node("BULL", "DISAGREE", [])])
    llm.script("DEBATE_BEAR", [_node("BEAR", "DISAGREE", [])])
    result = await run_debate(llm, _bundle(), sink=[])
    assert result.rounds_run == 1
    assert result.terminated_early
    assert llm.calls == 2
    assert result.conviction == pytest.approx(CONVICTION_UNANIMOUS_DISAGREE_FLOOR)


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


# --- conviction() (docs/day4_track_ab_plan.md §2.1) -----------------------

def test_conviction_unanimous_commit() -> None:
    keys = _bundle().keys()
    cites = ["quant.vrp_ratio", "quant.skew_abs", "quant.rsi"]
    nodes = (_node("BULL", "COMMIT", cites), _node("BEAR", "COMMIT", cites))
    assert conviction(nodes, keys) == pytest.approx(1.0)


def test_conviction_ungrounded_commit_is_haircut() -> None:
    """D6: both COMMIT with zero valid citations is a haircut, not a no-trade."""
    keys = _bundle().keys()
    nodes = (_node("BULL", "COMMIT", ["made up"]), _node("BEAR", "COMMIT", ["also made up"]))
    assert conviction(nodes, keys) == pytest.approx(0.75)


def test_conviction_split() -> None:
    # Both fully grounded (grounding=1.0) isolates the assertion to
    # commit_ratio=0.5's effect: c = 0.5 * (FLOOR + (1-FLOOR)*1.0) = 0.5.
    keys = _bundle().keys()
    cites = ["quant.vrp_ratio", "quant.skew_abs", "quant.rsi"]
    nodes = (_node("BULL", "COMMIT", cites), _node("BEAR", "DISAGREE", cites))
    assert conviction(nodes, keys) == pytest.approx(0.5)


def test_conviction_unanimous_disagree() -> None:
    """2026-08-31 pre-market unblock: floored, not zeroed -- see
    test_unanimous_disagree_terminates_round_1_at_floor_conviction."""
    keys = _bundle().keys()
    nodes = (_node("BULL", "DISAGREE", []), _node("BEAR", "DISAGREE", []))
    assert conviction(nodes, keys) == pytest.approx(CONVICTION_UNANIMOUS_DISAGREE_FLOOR)


def test_conviction_ignores_missing_nodes() -> None:
    keys = _bundle().keys()
    missing_bull = DebateNodeOutput(
        agent_persona="BULL", doc_action="DISAGREE", evidence_cited=[],
        volatility_view="(no response -- provider call failed or was dropped)", rebuttal_argument="x",
    )
    missing_bear = DebateNodeOutput(
        agent_persona="BEAR", doc_action="DISAGREE", evidence_cited=[],
        volatility_view="(no response -- provider call failed or was dropped)", rebuttal_argument="x",
    )
    # total outage -> defer to the deterministic gate, never a fabricated veto.
    assert conviction((missing_bull, missing_bear), keys) == 1.0

    cites = ["quant.vrp_ratio", "quant.skew_abs", "quant.rsi"]
    real_bull = _node("BULL", "COMMIT", cites)
    assert conviction((real_bull, missing_bear), keys) >= 0.5


async def test_single_bear_disagree_no_longer_vetoes() -> None:
    """D5 regression: a lone BEAR DISAGREE used to cap consensus_score at 0.65,
    an absolute veto. It must now only ever haircut conviction, never zero it,
    as long as the BULL commits."""
    llm = FakeLlm()
    cites = ["quant.vrp_ratio", "quant.skew_abs", "quant.rsi"]
    llm.script("DEBATE_BULL", [_node("BULL", "COMMIT", cites), _node("BULL", "COMMIT", cites)])
    llm.script("DEBATE_BEAR", [_node("BEAR", "DISAGREE", []), _node("BEAR", "DISAGREE", [])])
    result = await run_debate(llm, _bundle(), sink=[])
    assert result.conviction > 0.0


async def test_round_2_only_on_split() -> None:
    """Both unanimous-COMMIT and unanimous-DISAGREE terminate at round 1 --
    round 2 only fires for a genuine split (docs/day4_track_ab_plan.md §2.2)."""
    cites = ["quant.vrp_ratio", "quant.skew_abs", "quant.rsi"]

    commit_llm = FakeLlm()
    commit_llm.script("DEBATE_BULL", [_node("BULL", "COMMIT", cites)])
    commit_llm.script("DEBATE_BEAR", [_node("BEAR", "COMMIT", cites)])
    commit_result = await run_debate(commit_llm, _bundle(), sink=[])
    assert commit_result.rounds_run == 1
    assert commit_llm.calls == 2

    disagree_llm = FakeLlm()
    disagree_llm.script("DEBATE_BULL", [_node("BULL", "DISAGREE", [])])
    disagree_llm.script("DEBATE_BEAR", [_node("BEAR", "DISAGREE", [])])
    disagree_result = await run_debate(disagree_llm, _bundle(), sink=[])
    assert disagree_result.rounds_run == 1
    assert disagree_llm.calls == 2


def test_valid_citations_matches_bare_segment() -> None:
    keys = _bundle().keys()
    assert valid_citations(_node("BULL", "COMMIT", ["vrp_ratio"]), keys) == 1
    assert valid_citations(_node("BULL", "COMMIT", ["the IV/RV ratio"]), keys) == 0
    assert valid_citations(_node("BULL", "COMMIT", ["completely made up"]), keys) == 0

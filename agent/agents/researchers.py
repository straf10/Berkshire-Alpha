from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, Sequence

from agent.agents.evidence import EvidenceBundle
from agent.agents.prompts import BEAR_SYSTEM, BULL_SYSTEM, doc_proposition
from agent.config import (
    CONSENSUS_HIGH_THRESHOLD,
    CONVICTION_DEGRADED_FLOOR,
    CONVICTION_GROUNDING_FLOOR,
    DEBATE_MAX_ROUNDS,
    EVIDENCE_CITES_EXPECTED,
)
from agent.schemas.llm import DebateNodeOutput
from agent.tools.llm import LlmBudgetExceeded, LlmPort, LlmUnavailable, LlmValidationDropped

# The DoC protocol's safety-critical line: a node that never responds must
# never be treated as absent (which would let a lone COMMIT clear the
# threshold). It is synthesised as DISAGREE with zero citations -- excluded
# from conviction()'s "real" nodes so an outage defers to the deterministic
# layer instead of manufacturing a bearish verdict (docs/day4_track_ab_plan.md
# §2.1).
_MISSING_VIEW = "(no response -- provider call failed or was dropped)"

# Segments this short false-positive substring-match too easily ("rsi" inside
# "chris" etc.) -- require a word boundary for these (docs/day4_track_ab_plan.md §2.5).
_SHORT_SEGMENT_LEN = 5


class Verdict(StrEnum):
    CONSENSUS_ROUND_1 = "CONSENSUS_ROUND_1"
    CONSENSUS_ROUND_2 = "CONSENSUS_ROUND_2"
    UNRESOLVED = "UNRESOLVED"


@dataclass(frozen=True)
class DebateResult:
    nodes: tuple[DebateNodeOutput, ...]      # 2 or 4, in emission order
    rounds_run: int
    consensus_score: float
    verdict: Verdict
    terminated_early: bool
    # Day 4 (docs/day4_track_ab_plan.md §2.1): the size multiplier that
    # replaces `verdict` as control flow. consensus_score/verdict are kept
    # for logging and the dashboard only.
    conviction: float


def _missing_node(persona: Literal["BULL", "BEAR"]) -> DebateNodeOutput:
    return DebateNodeOutput(
        agent_persona=persona, doc_action="DISAGREE", evidence_cited=[],
        volatility_view=_MISSING_VIEW, rebuttal_argument=_MISSING_VIEW,
    )


def is_missing_node(node: DebateNodeOutput) -> bool:
    """True iff this node was synthesised because its real call dropped or
    was unavailable, rather than an actual model response -- used by
    pipeline.py to decide 'llm' vs 'llm-degraded' mode (docs/day3_llm_plan.md
    Group 5)."""
    return node.volatility_view == _MISSING_VIEW


def valid_citations(node: DebateNodeOutput, keys: frozenset[str]) -> int:
    """Count of node.evidence_cited entries matching a bundle key, either the
    full dotted key ("quant.vrp_ratio") or its bare last segment
    ("vrp_ratio") case-insensitively -- a model writing "the IV/RV ratio of
    1.31" cites nothing, but "the vrp_ratio of 1.31" does
    (docs/day4_track_ab_plan.md §2.5). Segments shorter than
    `_SHORT_SEGMENT_LEN` (e.g. "rsi") require a word-boundary match so they
    don't substring-match inside unrelated words. Fabricated citations still
    score zero -- this is what makes DoC enforceable in code rather than by
    prompt."""
    lowered_keys = tuple(k.lower() for k in keys)
    segments = tuple(k.rsplit(".", 1)[-1] for k in lowered_keys)
    count = 0
    for cite in node.evidence_cited:
        c = cite.lower()
        if any(k in c for k in lowered_keys):
            count += 1
            continue
        for seg in segments:
            if len(seg) < _SHORT_SEGMENT_LEN:
                if re.search(rf"\b{re.escape(seg)}\b", c):
                    count += 1
                    break
            elif seg in c:
                count += 1
                break
    return count


def conviction(nodes: Sequence[DebateNodeOutput], keys: frozenset[str]) -> float:
    """Debate outcome scales position size; it never sets it. Returns [0.0, 1.0]
    (docs/day4_track_ab_plan.md §2.1).

    Synthesised nodes are EXCLUDED: `_missing_node` fabricates a DISAGREE when a
    provider call fails, and an LLM outage must degrade to the deterministic
    layer, never to a fabricated unanimous bearish verdict.
    """
    real = [n for n in nodes if not is_missing_node(n)]
    if not real:
        return 1.0                                   # total outage -> defer to the gate
    commit_ratio = sum(n.doc_action == "COMMIT" for n in real) / len(real)
    grounding = sum(min(valid_citations(n, keys), EVIDENCE_CITES_EXPECTED)
                    for n in real) / (EVIDENCE_CITES_EXPECTED * len(real))
    # Grounding is a haircut, never a veto -- this is what keeps the DoC citation
    # check meaningful without reintroducing the D6 pathology.
    c = commit_ratio * (CONVICTION_GROUNDING_FLOOR
                         + (1.0 - CONVICTION_GROUNDING_FLOOR) * grounding)
    if len(real) < 2 and commit_ratio > 0:
        c = max(c, CONVICTION_DEGRADED_FLOOR)         # one voice may halve, never veto
    return c


def consensus_score(bull: DebateNodeOutput, bear: DebateNodeOutput, keys: frozenset[str]) -> float:
    """[NEW] docs/day3_llm_plan.md S0.4. Range [0, 1]."""
    commit = 0.5 * (int(bull.doc_action == "COMMIT") + int(bear.doc_action == "COMMIT"))
    grounding = 0.5 * sum(
        min(valid_citations(n, keys), EVIDENCE_CITES_EXPECTED) / EVIDENCE_CITES_EXPECTED
        for n in (bull, bear)
    )
    return 0.70 * commit + 0.30 * grounding


async def run_debate(llm: LlmPort, bundle: EvidenceBundle, *, sink: list[int]) -> DebateResult:
    """Round 1: BULL (bundle) then BEAR (bundle + bull's output).
    docs/day4_track_ab_plan.md §2.2 -- round 2 triggers on round-1 doc_action
    agreement, not on the (now advisory) consensus_score: unanimous COMMIT or
    unanimous DISAGREE both terminate at round 1 (nothing to litigate either
    way); a split runs round 2. Capped at DEBATE_MAX_ROUNDS=2; nothing extends
    it. A dropped/unavailable node counts as DISAGREE with 0 citations for
    consensus_score, but is excluded from conviction()'s "real" nodes so an
    outage never manufactures a fabricated bearish verdict."""
    keys = bundle.keys()
    evidence_json = bundle.to_prompt_json()
    structure = bundle.regime.structure
    proposition = doc_proposition(
        bundle.symbol, structure.value if structure else "UNKNOWN", str(bundle.quant.target_expiry)
    )
    citable_keys = ", ".join(sorted(keys))

    async def _turn(persona: Literal["BULL", "BEAR"], system: str, opposing: DebateNodeOutput | None) -> DebateNodeOutput:
        opposing_block = f"\nThe opposing researcher argued: {opposing.model_dump_json()}" if opposing else ""
        prompt = (
            f"{proposition}\n"
            f"Evidence bundle: {evidence_json}\n"
            f"Citable evidence keys: {citable_keys}\n"
            f"Cite exactly {EVIDENCE_CITES_EXPECTED} of them by their exact key."
            f"{opposing_block}"
        )
        node = "DEBATE_BULL" if persona == "BULL" else "DEBATE_BEAR"
        try:
            return await llm.complete_json(prompt, DebateNodeOutput, node=node, system=system, sink=sink)
        except LlmBudgetExceeded:
            raise
        except (LlmValidationDropped, LlmUnavailable):
            return _missing_node(persona)

    bull_r1 = await _turn("BULL", BULL_SYSTEM, None)
    bear_r1 = await _turn("BEAR", BEAR_SYSTEM, bull_r1)
    score_r1 = consensus_score(bull_r1, bear_r1, keys)
    commit_ratio_r1 = 0.5 * (int(bull_r1.doc_action == "COMMIT") + int(bear_r1.doc_action == "COMMIT"))

    if commit_ratio_r1 in (0.0, 1.0) or DEBATE_MAX_ROUNDS < 2:
        nodes = (bull_r1, bear_r1)
        verdict = Verdict.CONSENSUS_ROUND_1 if commit_ratio_r1 == 1.0 else Verdict.UNRESOLVED
        return DebateResult(
            nodes=nodes, rounds_run=1, consensus_score=score_r1,
            verdict=verdict, terminated_early=commit_ratio_r1 in (0.0, 1.0),
            conviction=conviction(nodes, keys),
        )

    bull_r2 = await _turn("BULL", BULL_SYSTEM, bear_r1)
    bear_r2 = await _turn("BEAR", BEAR_SYSTEM, bull_r2)
    score_r2 = consensus_score(bull_r2, bear_r2, keys)
    verdict = Verdict.CONSENSUS_ROUND_2 if score_r2 >= CONSENSUS_HIGH_THRESHOLD else Verdict.UNRESOLVED
    round2_nodes = (bull_r2, bear_r2)
    return DebateResult(
        nodes=(bull_r1, bear_r1, bull_r2, bear_r2), rounds_run=2, consensus_score=score_r2,
        verdict=verdict, terminated_early=False,
        conviction=conviction(round2_nodes, keys),
    )

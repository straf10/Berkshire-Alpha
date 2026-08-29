"""The orchestrator (docs/day3_llm_plan.md Group 5). scan_cycle is already a
~110-line, 10-branch loop; inlining analysts -> debate -> trader -> risk here
would roughly double it and would put LLM orchestration inside the module
that also owns the deterministic loop, the CLI calls, and the order walk.
This module keeps agent/main.py's diff small and keeps test_agents_never_execute
enforceable -- run_llm_pipeline returns values; agent/main.py persists and
executes."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import date
from typing import Mapping, Sequence

from agent.agents.analysts import AnalystResult, analyst_score, run_analysts, select_top
from agent.agents.researchers import DebateResult, Verdict, is_missing_node, run_debate
from agent.agents.risk_team import AccountView, PortfolioView, RiskTeamResult, run_risk_team
from agent.agents.trader import ProposalFailure, propose
from agent.schemas.execution import SpreadPlan
from agent.strategy.ticker_screener import ScreenedCandidate
from agent.tools.llm import LlmPort
from agent.tools.market_data import ChainCache
from agent.tools.news import Headline
from agent.tools.reddit import MentionSignal


@dataclass(frozen=True)
class AnalystArtifact:
    symbol: str
    analyst: str            # QUANT | NEWS | SENTIMENT
    ok: bool
    output_json: str | None
    error: str | None


@dataclass(frozen=True)
class DebateArtifact:
    round: int
    persona: str             # BULL | BEAR
    doc_action: str
    evidence_cited_json: str
    volatility_view: str
    rebuttal_argument: str


@dataclass(frozen=True)
class DebateSummaryArtifact:
    rounds_run: int
    consensus_score: float
    verdict: str
    terminated_early: bool


@dataclass(frozen=True)
class ProposalArtifact:
    proposal_json: str
    accepted: bool
    reject_reason: str | None


@dataclass(frozen=True)
class RiskVoteArtifact:
    persona: str
    decision: str
    max_loss_acceptable: bool
    risk_reward_ratio_acceptable: bool
    manager_notes: str


@dataclass(frozen=True)
class PipelineArtifacts:
    """Everything to persist once decision_id exists (docs/day3_llm_plan.md
    S1c). Deliberately NOT storage.write's Row dataclasses -- those require a
    decision_id this module can never know (agent/agents/* may not import
    agent.storage.write, and decision_id doesn't exist until after the
    deterministic gate runs anyway). agent/main.py converts these to Row
    objects at persist time."""

    analyst_rows: tuple[AnalystArtifact, ...] = ()
    debate_nodes: tuple[DebateArtifact, ...] = ()
    debate_summary: DebateSummaryArtifact | None = None
    proposal_row: ProposalArtifact | None = None
    risk_rows: tuple[RiskVoteArtifact, ...] = ()
    llm_call_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class PipelineOutcome:
    symbol: str
    plan: SpreadPlan | None            # None => this candidate is a no-trade
    mode: str                          # 'llm' | 'llm-degraded'
    reason: str                        # DEBATE_UNRESOLVED | RISK_TEAM_VETO | ProposalFailure member | NOT_TOP_DEBATE_CANDIDATE | 'OK'
    artifacts: PipelineArtifacts
    # main.py's DoD print line ('Analysts: ... score N.NN') reads this
    # directly rather than recomputing it from artifacts.analyst_rows, which
    # would require re-parsing every analyst's output_json back into a
    # Pydantic model just to re-derive a number already computed once here.
    # No default: a construction site that forgets to pass this must fail
    # loudly at construction, not silently print 'score 0.00'.
    analyst_score: float


_ANALYST_FIELDS: tuple[tuple[str, str], ...] = (
    ("QUANT", "quant_analyst"), ("NEWS", "news_analyst"), ("SENTIMENT", "sentiment_analyst"),
)


def _analyst_artifacts(r: AnalystResult) -> tuple[AnalystArtifact, ...]:
    failures = dict(r.failures)
    rows = []
    for name, field in _ANALYST_FIELDS:
        output = getattr(r.bundle, field)
        if output is not None:
            rows.append(AnalystArtifact(symbol=r.symbol, analyst=name, ok=True, output_json=output.model_dump_json(), error=None))
        else:
            rows.append(AnalystArtifact(symbol=r.symbol, analyst=name, ok=False, output_json=None, error=failures.get(name)))
    return tuple(rows)


def _debate_artifacts(debate: DebateResult) -> tuple[DebateArtifact, ...]:
    return tuple(
        DebateArtifact(
            round=1 if i < 2 else 2, persona=node.agent_persona, doc_action=node.doc_action,
            evidence_cited_json=json.dumps(node.evidence_cited), volatility_view=node.volatility_view,
            rebuttal_argument=node.rebuttal_argument,
        )
        for i, node in enumerate(debate.nodes)
    )


def _debate_summary_artifact(debate: DebateResult) -> DebateSummaryArtifact:
    return DebateSummaryArtifact(
        rounds_run=debate.rounds_run, consensus_score=debate.consensus_score,
        verdict=debate.verdict.value, terminated_early=debate.terminated_early,
    )


def _risk_vote_artifacts(result: RiskTeamResult) -> tuple[RiskVoteArtifact, ...]:
    return tuple(
        RiskVoteArtifact(
            persona=v.persona, decision=v.decision, max_loss_acceptable=v.max_loss_acceptable,
            risk_reward_ratio_acceptable=v.risk_reward_ratio_acceptable, manager_notes=v.manager_notes,
        )
        for v in result.votes
    )


def _mode(r: AnalystResult, debate: DebateResult) -> str:
    """'llm' iff all three analysts and both debate turns of every round run
    produced a real response; 'llm-degraded' if anything was dropped but the
    pipeline still proceeded (docs/day3_llm_plan.md Group 5)."""
    degraded = bool(r.failures) or any(is_missing_node(n) for n in debate.nodes)
    return "llm-degraded" if degraded else "llm"


async def run_llm_pipeline(
    llm: LlmPort, candidates: Sequence[ScreenedCandidate], chains: ChainCache,
    news: Mapping[str, tuple[Headline, ...]], mentions: Mapping[str, MentionSignal],
    account: AccountView, portfolio: PortfolioView, trading_days: frozenset[date],
    *, sem: asyncio.Semaphore, sinks: Mapping[str, list[int]],
) -> list[PipelineOutcome]:
    """1. run_analysts over all shortlisted candidates (<=12 calls, one gather)
       2. rank by analyst_score, take DEBATE_CANDIDATES (2)
       3. per surviving candidate, concurrently with the others:
            run_debate -> UNRESOLVED? stop, no_trade
            propose    -> ProposalFailure? stop, no_trade
            run_risk_team -> vetoed? stop, no_trade
       4. return one PipelineOutcome per SHORTLISTED candidate (not just the
          top 2) so every name still gets a decisions row.
       Raises LlmUnavailable / LlmBudgetExceeded only; every other failure is
       already isolated to its node by the layers below."""
    analyst_results = await run_analysts(llm, candidates, news, mentions, sem=sem, sinks=sinks)
    debated_symbols = {r.symbol for r in select_top(analyst_results, candidates)}

    async def _survivor(r: AnalystResult) -> PipelineOutcome:
        symbol = r.symbol
        sink = sinks[symbol]
        analyst_artifacts = _analyst_artifacts(r)

        debate = await run_debate(llm, r.bundle, sink=sink)
        mode = _mode(r, debate)

        if debate.verdict == Verdict.UNRESOLVED:
            return PipelineOutcome(
                symbol=symbol, plan=None, mode=mode, reason="DEBATE_UNRESOLVED",
                analyst_score=analyst_score(r),
                artifacts=PipelineArtifacts(
                    analyst_rows=analyst_artifacts, debate_nodes=_debate_artifacts(debate),
                    debate_summary=_debate_summary_artifact(debate), llm_call_ids=tuple(sink),
                ),
            )

        chain = chains.get(symbol)
        proposal_result = (
            await propose(llm, r.bundle, debate, chain, trading_days, sink=sink)
            if chain is not None else ProposalFailure.STRIKE_NOT_IN_CHAIN
        )

        if isinstance(proposal_result, ProposalFailure):
            return PipelineOutcome(
                symbol=symbol, plan=None, mode=mode, reason=proposal_result.value,
                analyst_score=analyst_score(r),
                artifacts=PipelineArtifacts(
                    analyst_rows=analyst_artifacts, debate_nodes=_debate_artifacts(debate),
                    debate_summary=_debate_summary_artifact(debate), llm_call_ids=tuple(sink),
                ),
            )

        proposal, plan = proposal_result
        risk_result = await run_risk_team(llm, plan, r.bundle, account, portfolio, sem=sem, sink=sink)

        if risk_result.vetoed:
            return PipelineOutcome(
                symbol=symbol, plan=None, mode=mode, reason="RISK_TEAM_VETO",
                analyst_score=analyst_score(r),
                artifacts=PipelineArtifacts(
                    analyst_rows=analyst_artifacts, debate_nodes=_debate_artifacts(debate),
                    debate_summary=_debate_summary_artifact(debate),
                    proposal_row=ProposalArtifact(proposal_json=proposal.model_dump_json(), accepted=False, reject_reason="RISK_TEAM_VETO"),
                    risk_rows=_risk_vote_artifacts(risk_result), llm_call_ids=tuple(sink),
                ),
            )

        return PipelineOutcome(
            symbol=symbol, plan=plan, mode=mode, reason="OK",
            analyst_score=analyst_score(r),
            artifacts=PipelineArtifacts(
                analyst_rows=analyst_artifacts, debate_nodes=_debate_artifacts(debate),
                debate_summary=_debate_summary_artifact(debate),
                proposal_row=ProposalArtifact(proposal_json=proposal.model_dump_json(), accepted=True, reject_reason=None),
                risk_rows=_risk_vote_artifacts(risk_result), llm_call_ids=tuple(sink),
            ),
        )

    survivors = [r for r in analyst_results if r.symbol in debated_symbols]
    others = [r for r in analyst_results if r.symbol not in debated_symbols]

    # return_exceptions=True: a bare gather() propagates the first exception
    # but does NOT cancel sibling tasks, so a LlmBudgetExceeded from one
    # candidate would leave another's _survivor still running after
    # scan_cycle's caller has moved on and closed the DB connection.
    survivor_outcomes = await asyncio.gather(*(_survivor(r) for r in survivors), return_exceptions=True)
    first_exception = next((o for o in survivor_outcomes if isinstance(o, BaseException)), None)
    if first_exception is not None:
        raise first_exception

    other_outcomes = [
        PipelineOutcome(
            # NOT_TOP_DEBATE_CANDIDATE never reaches the debate stage, so the
            # spec's "both debate rounds produced valid output" clause for
            # mode='llm' does not apply here by construction -- 'llm' means
            # only that this candidate's own analysts all succeeded.
            symbol=r.symbol, plan=None, mode="llm-degraded" if r.failures else "llm",
            reason="NOT_TOP_DEBATE_CANDIDATE", analyst_score=analyst_score(r),
            artifacts=PipelineArtifacts(analyst_rows=_analyst_artifacts(r), llm_call_ids=tuple(sinks[r.symbol])),
        )
        for r in others
    ]

    by_symbol = {o.symbol: o for o in (*survivor_outcomes, *other_outcomes)}
    return [by_symbol[c.snapshot.symbol] for c in candidates]

from __future__ import annotations

import json
from collections import Counter
from datetime import date
from enum import StrEnum
from typing import Literal

from agent.agents.evidence import EvidenceBundle
from agent.agents.prompts import TRADER_SYSTEM
from agent.agents.researchers import DebateResult
from agent.config import DTE_MAX, DTE_MIN, MAX_LEGS, STRIKE_TABLE_SPAN
from agent.schemas.execution import SpreadPlan, Structure
from agent.schemas.llm import SpreadProposal
from agent.schemas.market import ChainSnapshot, QuantSnapshot
from agent.strategy.regime import RegimeDecision
from agent.strategy.spread_builder import BuildFailure, build_from_proposal
from agent.tools.llm import LlmPort


class ProposalFailure(StrEnum):
    WRONG_UNDERLYING = "WRONG_UNDERLYING"
    EXPIRY_NOT_IN_WINDOW = "EXPIRY_NOT_IN_WINDOW"
    EXPIRY_NOT_TRADING_DAY = "EXPIRY_NOT_TRADING_DAY"
    STRIKE_NOT_IN_CHAIN = "STRIKE_NOT_IN_CHAIN"
    LEG_COUNT = "LEG_COUNT"
    STRUCTURE_MISMATCH = "STRUCTURE_MISMATCH"
    NOT_DEFINED_RISK = "NOT_DEFINED_RISK"


# OptionLegProposal.contract_type speaks CALL/PUT (plan.md's schema,
# transcribed verbatim); ChainSnapshot/OptionQuote speak the chain's C/P
# convention (Day 2). This is the one place that translates between them.
_RIGHT: dict[str, Literal["C", "P"]] = {"CALL": "C", "PUT": "P"}

# Maps (right, sell_strike > buy_strike) -> the structure it forms. Mirrors
# spread_builder.build()'s own strike-ordering logic exactly, so a proposal
# is only ever accepted if it describes the same vertical the deterministic
# builder would have (docs/day3_llm_plan.md Group 4).
_CREDIT_STRUCTURE: dict[Literal["C", "P"], Structure] = {
    "P": Structure.BULL_PUT_SPREAD, "C": Structure.BEAR_CALL_SPREAD,
}
_DEBIT_STRUCTURE: dict[Literal["C", "P"], Structure] = {
    "C": Structure.BULL_CALL_SPREAD, "P": Structure.BEAR_PUT_SPREAD,
}


def strike_table(
    chain: ChainSnapshot, expiry: date, right: Literal["C", "P"], spot: float, span: int = STRIKE_TABLE_SPAN
) -> tuple[dict, ...]:
    """<=24 rows: strike, bid, ask, delta -- the ONLY chain data the trader
    sees (S0.6). Bounded by construction: at most 2*span+1 distinct strikes,
    centred on the strike nearest spot, never by a dollar-distance filter
    that could admit an unbounded number of rows on a wide/sparse chain."""
    contracts = chain.for_expiry(expiry, right)
    if not contracts:
        return ()
    strikes = sorted({c.strike for c in contracts})
    center = min(range(len(strikes)), key=lambda i: abs(strikes[i] - spot))
    window = set(strikes[max(0, center - span) : center + span + 1])
    rows = sorted(
        ({"strike": c.strike, "bid": c.bid, "ask": c.ask, "delta": c.delta} for c in contracts if c.strike in window),
        key=lambda r: r["strike"],
    )
    return tuple(rows[:24])


def _infer_structure(legs: tuple) -> Structure | None:
    by_right: dict[str, list] = {}
    for leg in legs:
        by_right.setdefault(leg.contract_type, []).append(leg)
    if len(by_right) != 1:
        return None
    (right, right_legs), = by_right.items()
    if len(right_legs) != 2:
        return None
    sides = Counter(leg.side for leg in right_legs)
    if sides.get("BUY", 0) != 1 or sides.get("SELL", 0) != 1:
        return None
    buy = next(leg for leg in right_legs if leg.side == "BUY")
    sell = next(leg for leg in right_legs if leg.side == "SELL")
    if sell.strike_price == buy.strike_price:
        return None
    chain_right = _RIGHT[right]
    if sell.strike_price > buy.strike_price:
        return _CREDIT_STRUCTURE[chain_right]
    return _DEBIT_STRUCTURE[chain_right]


def validate_proposal(
    p: SpreadProposal, q: QuantSnapshot, d: RegimeDecision, chain: ChainSnapshot, trading_days: frozenset[date]
) -> ProposalFailure | None:
    """Pure. Underlying == q.symbol; expiry parses, is in trading_days, and
    DTE_MIN <= dte <= DTE_MAX; 2 <= legs <= MAX_LEGS; every (strike, right)
    resolves to a listed OCC symbol in `chain`; exactly one BUY and one SELL
    per right; the resulting structure equals d.structure."""
    if p.underlying != q.symbol:
        return ProposalFailure.WRONG_UNDERLYING

    try:
        expiry = date.fromisoformat(p.expiration_date)
    except ValueError:
        return ProposalFailure.EXPIRY_NOT_TRADING_DAY

    if expiry not in trading_days:
        return ProposalFailure.EXPIRY_NOT_TRADING_DAY

    dte = (expiry - q.session_date).days
    if not (DTE_MIN <= dte <= DTE_MAX):
        return ProposalFailure.EXPIRY_NOT_IN_WINDOW

    if not (2 <= len(p.legs) <= MAX_LEGS):
        return ProposalFailure.LEG_COUNT

    for leg in p.legs:
        listed = chain.for_expiry(expiry, _RIGHT[leg.contract_type])
        if not any(round(c.strike, 4) == round(leg.strike_price, 4) for c in listed):
            return ProposalFailure.STRIKE_NOT_IN_CHAIN

    by_right: dict[str, list] = {}
    for leg in p.legs:
        by_right.setdefault(leg.contract_type, []).append(leg)
    for right_legs in by_right.values():
        sides = Counter(leg.side for leg in right_legs)
        if sides.get("BUY", 0) != 1 or sides.get("SELL", 0) != 1:
            return ProposalFailure.STRUCTURE_MISMATCH
        if any(leg.ratio_qty != 1 for leg in right_legs):
            # build_from_proposal only ever prices a 1:1 vertical (matching
            # spread_builder.build()) -- a ratio leg is not a risk this build
            # path can size, so it is rejected here rather than silently
            # collapsed to 1:1.
            return ProposalFailure.NOT_DEFINED_RISK

    structure = _infer_structure(tuple(p.legs))
    if structure is None or structure != d.structure:
        return ProposalFailure.STRUCTURE_MISMATCH

    return None


def _trader_prompt(bundle: EvidenceBundle, debate: DebateResult, chain: ChainSnapshot, q: QuantSnapshot, d: RegimeDecision) -> str:
    structure = d.structure
    assert structure is not None and q.target_expiry is not None
    is_credit = structure in (Structure.BULL_PUT_SPREAD, Structure.BEAR_CALL_SPREAD)
    right: Literal["C", "P"] = "P" if structure in (Structure.BULL_PUT_SPREAD, Structure.BEAR_PUT_SPREAD) else "C"
    table = strike_table(chain, q.target_expiry, right, q.spot)
    debate_summary = {
        "verdict": debate.verdict.value, "consensus_score": round(debate.consensus_score, 3),
        "rounds_run": debate.rounds_run,
    }
    return (
        f"Underlying: {q.symbol}  Structure required: {structure.value}  "
        f"Expiry: {q.target_expiry.isoformat()}  Right: {right}  Credit spread: {is_credit}\n"
        f"Evidence: {bundle.to_prompt_json()}\n"
        f"Debate outcome: {json.dumps(debate_summary, separators=(',', ':'))}\n"
        f"Strikes available (strike,bid,ask,delta), choose ONLY from this table:\n{json.dumps(table, separators=(',', ':'))}"
    )


async def propose(
    llm: LlmPort, bundle: EvidenceBundle, debate: DebateResult, chain: ChainSnapshot,
    trading_days: frozenset[date], *, sink: list[int],
) -> tuple[SpreadProposal, SpreadPlan] | ProposalFailure:
    """One call. On a validation failure, ONE retry with the failure named in
    the prompt (a hallucinated strike is treated exactly like a
    ValidationError and consumes the single retry), then the candidate is
    dropped. On success, converts via spread_builder.build_from_proposal()."""
    q, d = bundle.quant, bundle.regime
    base_prompt = _trader_prompt(bundle, debate, chain, q, d)

    proposal = await llm.complete_json(base_prompt, SpreadProposal, node="TRADER", system=TRADER_SYSTEM, sink=sink)
    failure = validate_proposal(proposal, q, d, chain, trading_days)

    if failure is not None:
        retry_prompt = f"{base_prompt}\n\nYour previous proposal was rejected: {failure.value}. Propose again, correcting this."
        proposal = await llm.complete_json(retry_prompt, SpreadProposal, node="TRADER", system=TRADER_SYSTEM, sink=sink)
        failure = validate_proposal(proposal, q, d, chain, trading_days)
        if failure is not None:
            return failure

    plan = build_from_proposal(q, d, chain, proposal)
    if isinstance(plan, BuildFailure):
        return ProposalFailure.STRIKE_NOT_IN_CHAIN
    return proposal, plan

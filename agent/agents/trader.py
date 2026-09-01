from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Literal

from agent.agents.evidence import EvidenceBundle
from agent.agents.prompts import TRADER_SYSTEM
from agent.agents.researchers import DebateResult
from agent.config import DTE_MAX, DTE_MIN, MAX_LEGS, SHORT_DELTA_BAND, SHORT_DELTA_TARGET, STRIKE_TABLE_SPAN
from agent.schemas.execution import STRUCTURE_IS_CREDIT, SpreadPlan, Structure
from agent.schemas.llm import SpreadProposal
from agent.schemas.market import ChainSnapshot, QuantSnapshot
from agent.strategy.regime import RegimeDecision
from agent.strategy.spread_builder import BuildFailure, build, build_from_proposal
from agent.tools.llm import LlmPort


class ProposalFailure(StrEnum):
    WRONG_UNDERLYING = "WRONG_UNDERLYING"
    EXPIRY_NOT_IN_WINDOW = "EXPIRY_NOT_IN_WINDOW"
    EXPIRY_NOT_TRADING_DAY = "EXPIRY_NOT_TRADING_DAY"
    STRIKE_NOT_IN_CHAIN = "STRIKE_NOT_IN_CHAIN"
    LEG_COUNT = "LEG_COUNT"
    STRUCTURE_MISMATCH = "STRUCTURE_MISMATCH"
    NOT_DEFINED_RISK = "NOT_DEFINED_RISK"
    SHORT_DELTA_OUT_OF_BAND = "SHORT_DELTA_OUT_OF_BAND"


# OptionLegProposal.contract_type speaks CALL/PUT (plan.md's schema,
# transcribed verbatim); ChainSnapshot/OptionQuote speak the chain's C/P
# convention (Day 2). This is the one place that translates between them.
_RIGHT: dict[str, Literal["C", "P"]] = {"CALL": "C", "PUT": "P"}

# Maps (right, short_strike is further OTM than long_strike) -> the vertical
# those two legs actually form. Mirrors spread_builder.build()'s own strike
# ordering: for a CREDIT spread build() places the long leg FURTHER OTM than
# the short (`long = short.strike + direction * offset`, direction -1 for puts
# and +1 for calls), so the credit case is sell-above-buy on puts and
# sell-BELOW-buy on calls. The call side of this table was inverted before
# 2026-08-31 (both entries read as if a call credit spread were sold above the
# long leg), which made every BEAR_CALL_SPREAD proposal fail validation as
# STRUCTURE_MISMATCH no matter what the LLM proposed -- see memory.md's Day-1
# post-mortem, and 797efd4, which found and fixed the same inversion
# independently in `is_credit` form. Keyed by (right, sell_strike > buy_strike).
_VERTICAL_BY_ORDERING: dict[tuple[Literal["C", "P"], bool], Structure] = {
    ("P", True): Structure.BULL_PUT_SPREAD,    # sell the higher put  -> credit
    ("P", False): Structure.BEAR_PUT_SPREAD,   # sell the lower put   -> debit
    ("C", False): Structure.BEAR_CALL_SPREAD,  # sell the lower call  -> credit
    ("C", True): Structure.BULL_CALL_SPREAD,   # sell the higher call -> debit
}


# The exact leg recipe for each vertical, stated to the trader in words. The
# structure is decided deterministically by regime.select() before the model
# is ever called -- its only job is picking two listed strikes -- so leaving
# the buy/sell ordering implicit was pure downside: on 2026-08-31 every
# proposal that reached this stage was rejected, and the single retry named
# only the enum ("STRUCTURE_MISMATCH") without ever saying what shape was
# wanted (memory.md, Day-1 post-mortem).
_LEG_RECIPE: dict[Structure, str] = {
    Structure.BULL_PUT_SPREAD:
        "SELL the HIGHER-strike put and BUY the LOWER-strike put (net credit).",
    Structure.BEAR_CALL_SPREAD:
        "SELL the LOWER-strike call and BUY the HIGHER-strike call (net credit).",
    Structure.BULL_CALL_SPREAD:
        "BUY the LOWER-strike call and SELL the HIGHER-strike call (net debit).",
    Structure.BEAR_PUT_SPREAD:
        "BUY the HIGHER-strike put and SELL the LOWER-strike put (net debit).",
}

# What each rejection actually means, so the one retry is told how to fix the
# proposal rather than only which enum it tripped.
_FAILURE_HELP: dict[ProposalFailure, str] = {
    ProposalFailure.WRONG_UNDERLYING: "the underlying must be exactly the symbol named above",
    ProposalFailure.EXPIRY_NOT_IN_WINDOW: f"expiration_date must be the expiry named above ({DTE_MIN}-{DTE_MAX} DTE)",
    ProposalFailure.EXPIRY_NOT_TRADING_DAY: "expiration_date must be the exact expiry named above, as YYYY-MM-DD",
    ProposalFailure.STRIKE_NOT_IN_CHAIN: "every strike_price must be copied verbatim from the strike table above",
    ProposalFailure.LEG_COUNT: "propose exactly two legs",
    ProposalFailure.STRUCTURE_MISMATCH: "the two legs did not form the required structure",
    ProposalFailure.NOT_DEFINED_RISK: "both legs must have ratio_qty 1",
    ProposalFailure.SHORT_DELTA_OUT_OF_BAND: (
        f"the SELL leg's |delta| must be between {SHORT_DELTA_BAND[0]} and {SHORT_DELTA_BAND[1]} "
        f"(target {SHORT_DELTA_TARGET})"
    ),
}


def strike_table(
    chain: ChainSnapshot, expiry: date, right: Literal["C", "P"], spot: float, span: int = STRIKE_TABLE_SPAN,
    target_delta: float | None = None,
) -> tuple[dict, ...]:
    """<=24 rows: strike, bid, ask, delta -- the ONLY chain data the trader
    sees (S0.6). Bounded by construction: at most 2*span+1 distinct strikes,
    centred on the strike nearest spot, never by a dollar-distance filter
    that could admit an unbounded number of rows on a wide/sparse chain.

    `target_delta`, when given (credit structures -- Task 6, docs/
    audit_report_v2.md §7A root cause 2), centres the window on the strike
    whose |delta| is nearest that target instead of the strike nearest spot.
    On wide-grid names (GS $2.50, LLY $5) the spot-centred window can leave
    the compliant SHORT_DELTA_BAND strike outside the table entirely, making
    it unofferable to the model no matter how clearly the prompt states the
    requirement. None preserves the original spot-centred behaviour exactly."""
    contracts = chain.for_expiry(expiry, right)
    if not contracts:
        return ()
    strikes = sorted({c.strike for c in contracts})
    if target_delta is None:
        center = min(range(len(strikes)), key=lambda i: abs(strikes[i] - spot))
    else:
        delta_at = {c.strike: c.delta for c in contracts}
        center = min(range(len(strikes)), key=lambda i: abs(abs(delta_at[strikes[i]]) - target_delta))
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
    # Puts: the higher strike is worth more, so selling it is the credit side
    # (sell > buy). Calls: the LOWER strike is worth more, so selling it is
    # the credit side (sell < buy).
    return _VERTICAL_BY_ORDERING[(chain_right, sell.strike_price > buy.strike_price)]


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

    # P0 remediation (docs/audit_report_v2.md §7A / §9 item 5). SHORT_DELTA_BAND
    # had exactly one consumer in the whole codebase -- spread_builder's
    # deterministic build() -- so every LLM-picked credit spread bypassed it
    # entirely. All four live LLM credit trades on 2026-09-01 struck at
    # 0.486-0.609 delta against the (0.22, 0.33) band. Scoped to credit
    # structures only -- debit verticals have a different geometry and are
    # out of scope for this check.
    if STRUCTURE_IS_CREDIT[d.structure]:
        lo, hi = SHORT_DELTA_BAND
        sell = next(l for l in p.legs if l.side == "SELL")
        listed = chain.for_expiry(expiry, _RIGHT[sell.contract_type])
        short_q = next(
            (c for c in listed if round(c.strike, 4) == round(sell.strike_price, 4)), None
        )
        if short_q is None or not (lo < abs(short_q.delta) < hi):
            return ProposalFailure.SHORT_DELTA_OUT_OF_BAND

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
    table = strike_table(chain, q.target_expiry, right, q.spot, target_delta=SHORT_DELTA_TARGET if is_credit else None)
    debate_summary = {
        "verdict": debate.verdict.value, "consensus_score": round(debate.consensus_score, 3),
        "rounds_run": debate.rounds_run,
    }
    # Task 6a (docs/audit_report_v2.md §7A root cause 1): the prompt never
    # stated the target delta, so the model optimised the one thing it could
    # see -- premium -- and struck every credit spread near 0.50-0.60 delta
    # against a 0.22-0.33 band.
    delta_requirement = (
        f"SHORT LEG REQUIREMENT: the SELL leg's |delta| MUST be between {SHORT_DELTA_BAND[0]} and "
        f"{SHORT_DELTA_BAND[1]} (target {SHORT_DELTA_TARGET}). Proposals outside this band are rejected.\n"
        if is_credit else ""
    )
    return (
        f"Underlying: {q.symbol}  Structure required: {structure.value}  "
        f"Expiry: {q.target_expiry.isoformat()}  Right: {right}  Credit spread: {is_credit}\n"
        f"Required legs: {_LEG_RECIPE[structure]} Both legs are {'CALL' if right == 'C' else 'PUT'}s, "
        f"ratio_qty 1, expiring {q.target_expiry.isoformat()}. Spot is {q.spot}.\n"
        f"{delta_requirement}"
        f"Evidence: {bundle.to_prompt_json()}\n"
        f"Debate outcome: {json.dumps(debate_summary, separators=(',', ':'))}\n"
        f"Strikes available (strike,bid,ask,delta), choose ONLY from this table:\n{json.dumps(table, separators=(',', ':'))}"
    )


@dataclass(frozen=True)
class ProposalOutcome:
    """`proposal` is the model's own, or None when the deterministic builder
    picked the strikes instead -- in which case `fallback_from` is the
    rejection the model could not correct. Carried through so the decision log
    records WHICH path chose the contracts rather than presenting a
    deterministic pick as the model's."""
    proposal: SpreadProposal | None
    plan: SpreadPlan
    fallback_from: ProposalFailure | None = None


async def propose(
    llm: LlmPort, bundle: EvidenceBundle, debate: DebateResult, chain: ChainSnapshot,
    trading_days: frozenset[date], *, sink: list[int],
) -> ProposalOutcome | ProposalFailure:
    """One call. On a validation failure, ONE retry with the failure explained
    in the prompt (a hallucinated strike is treated exactly like a
    ValidationError and consumes the single retry). On success, converts via
    spread_builder.build_from_proposal().

    If the retry also fails, the candidate is NOT dropped: spread_builder.build()
    -- the deterministic Day-2 path, delta-band short leg and all -- picks the
    strikes instead, and the outcome records `fallback_from`. Rationale: by this
    point the regime selector, the analysts, the debate and the conviction floor
    have all already decided this name is worth trading; a model that cannot
    format two strikes is a formatting failure, not a trading signal, and it was
    the sole reason the agent traded nothing on 2026-08-31. Every downstream
    control is unchanged -- the risk team still votes and the deterministic gate
    still sizes and can still reject. If build() also declines, the original
    ProposalFailure is returned and the candidate really does drop."""
    q, d = bundle.quant, bundle.regime
    base_prompt = _trader_prompt(bundle, debate, chain, q, d)

    proposal = await llm.complete_json(base_prompt, SpreadProposal, node="TRADER", system=TRADER_SYSTEM, sink=sink)
    failure = validate_proposal(proposal, q, d, chain, trading_days)

    if failure is not None:
        rejected = json.dumps([leg.model_dump() for leg in proposal.legs], separators=(",", ":"))
        retry_prompt = (
            f"{base_prompt}\n\nYour previous proposal {rejected} was rejected "
            f"({failure.value}): {_FAILURE_HELP[failure]}. "
            f"Required: {_LEG_RECIPE[d.structure]} Propose again, correcting this."
        )
        proposal = await llm.complete_json(retry_prompt, SpreadProposal, node="TRADER", system=TRADER_SYSTEM, sink=sink)
        failure = validate_proposal(proposal, q, d, chain, trading_days)
        if failure is not None:
            return _deterministic_fallback(q, d, chain, failure)

    plan = build_from_proposal(q, d, chain, proposal)
    if isinstance(plan, BuildFailure):
        return _deterministic_fallback(q, d, chain, ProposalFailure.STRIKE_NOT_IN_CHAIN)
    return ProposalOutcome(proposal=proposal, plan=plan)


def _deterministic_fallback(
    q: QuantSnapshot, d: RegimeDecision, chain: ChainSnapshot, failure: ProposalFailure,
) -> ProposalOutcome | ProposalFailure:
    """spread_builder.build() -- the same delta-band rule the quant-only spine
    uses -- picks the strikes the model could not. Returns the original failure
    unchanged if build() also declines, so a genuinely untradeable chain still
    drops the candidate."""
    plan = build(q, d, chain)
    if isinstance(plan, BuildFailure):
        return failure
    return ProposalOutcome(proposal=None, plan=plan, fallback_from=failure)

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Final, Mapping, Sequence

from agent.agents.evidence import EvidenceBundle
from agent.agents.prompts import NEWS_ANALYST_SYSTEM, QUANT_ANALYST_SYSTEM, SENTIMENT_ANALYST_SYSTEM
from agent.config import DEBATE_CANDIDATES, NEWS_MAX_HEADLINES, SENTIMENT_MAX_POSTS_IN_PROMPT, UNIVERSE
from agent.schemas.execution import STRUCTURE_IS_CREDIT, Structure
from agent.schemas.llm import NewsAnalystOutput, QuantAnalystOutput, SentimentAnalystOutput
from agent.schemas.market import QuantSnapshot
from agent.strategy.regime import RegimeDecision
from agent.strategy.ticker_screener import ScreenedCandidate
from agent.tools.llm import LlmBudgetExceeded, LlmPort, LlmUnavailable, LlmValidationDropped
from agent.tools.news import Headline
from agent.tools.reddit import MentionSignal

logger = logging.getLogger(__name__)

DIRECTION: Final[dict[Structure, int]] = {
    Structure.BULL_PUT_SPREAD: +1, Structure.BULL_CALL_SPREAD: +1,
    Structure.BEAR_CALL_SPREAD: -1, Structure.BEAR_PUT_SPREAD: -1,
}

_MOMENTUM_SIGN: Final[dict[str, int]] = {
    "STRONG_UP": 1, "WEAK_UP": 1, "NEUTRAL": 0, "WEAK_DOWN": -1, "STRONG_DOWN": -1,
}
_IMPACT_SIGN: Final[dict[str, int]] = {"BULLISH": 1, "NEUTRAL": 0, "BEARISH": -1}


async def quant_analyst(llm: LlmPort, q: QuantSnapshot, d: RegimeDecision, *, sink: list[int]) -> QuantAnalystOutput:
    prompt = (
        f"Underlying: {q.symbol}\n"
        f"VRP_ratio={q.vrp_ratio:.3f} Skew_Abs={q.skew_abs:.2f} VWAP_dev_pct={q.vwap_dev_pct:.2f} "
        f"RSI5={q.rsi:.1f} VWM_z={q.vwm_z:.2f}\n"
        f"Deterministic regime read: {d.regime.value} ({d.reason})."
    )
    return await llm.complete_json(prompt, QuantAnalystOutput, node="QUANT", system=QUANT_ANALYST_SYSTEM, sink=sink)


async def news_analyst(
    llm: LlmPort, symbol: str, headlines: Sequence[Headline], *, sink: list[int]
) -> NewsAnalystOutput | None:
    """No headlines -> no call: asking a model to opine on '(none)' burns a
    call and buys nothing. A skipped call is not a failure -- it leaves the
    bundle field None exactly like a dropped call does, and analyst_score
    already treats a missing news analyst as neutral (0.5)."""
    if not headlines:
        return None
    lines = "\n".join(f"[{h.id}] {h.headline} -- {h.summary}" for h in headlines[:NEWS_MAX_HEADLINES])
    prompt = f"Ticker: {symbol}\nRecent headlines:\n{lines}"
    return await llm.complete_json(prompt, NewsAnalystOutput, node="NEWS", system=NEWS_ANALYST_SYSTEM, sink=sink)


async def sentiment_analyst(
    llm: LlmPort, symbol: str, signal: MentionSignal | None, *, sink: list[int]
) -> SentimentAnalystOutput | None:
    """No posts -> no call, same reasoning as news_analyst above."""
    if signal is None or not signal.posts:
        return None
    titles = "\n".join(f"- {p.title[:160]}" for p in signal.posts[:SENTIMENT_MAX_POSTS_IN_PROMPT])
    mentions, velocity = signal.mentions, signal.velocity
    prompt = f"Ticker: {symbol}\nMentions this scan: {mentions}  Velocity: {velocity:.2f}\nRecent post titles:\n{titles}"
    return await llm.complete_json(
        prompt, SentimentAnalystOutput, node="SENTIMENT", system=SENTIMENT_ANALYST_SYSTEM, sink=sink
    )


@dataclass(frozen=True)
class AnalystResult:
    symbol: str
    bundle: EvidenceBundle
    failures: tuple[tuple[str, str], ...]      # (analyst, error class) -- persisted, shown in the feed


async def run_analysts(
    llm: LlmPort, candidates: Sequence[ScreenedCandidate],
    news: Mapping[str, tuple[Headline, ...]], mentions: Mapping[str, MentionSignal],
    *, sem: asyncio.Semaphore, sinks: Mapping[str, list[int]],
) -> list[AnalystResult]:
    """2 x len(candidates) calls, ONE asyncio.gather, bounded by `sem`. Never
    raises except LlmBudgetExceeded (propagates immediately) or LlmUnavailable
    when >= half the wave failed (docs/day3_llm_plan.md Group 3).

    `sentiment_analyst` is never invoked here (docs/day4_action_plan.md Step
    1): Reddit's API is closed to us, so `mentions` is always empty and the
    call would be a guaranteed no-op. `sentiment_analyst()` itself stays,
    unused, in case the API ever reopens."""

    async def _bounded(coro):
        async with sem:
            return await coro

    tasks = []
    meta: list[tuple[str, str]] = []
    for c in candidates:
        symbol = c.snapshot.symbol
        sink = sinks[symbol]
        tasks.append(_bounded(quant_analyst(llm, c.snapshot, c.decision, sink=sink)))
        meta.append((symbol, "QUANT"))
        tasks.append(_bounded(news_analyst(llm, symbol, news.get(symbol, ()), sink=sink)))
        meta.append((symbol, "NEWS"))

    results = await asyncio.gather(*tasks, return_exceptions=True) if tasks else []

    by_symbol: dict[str, dict[str, object]] = {c.snapshot.symbol: {} for c in candidates}
    failures_by_symbol: dict[str, list[tuple[str, str]]] = {c.snapshot.symbol: [] for c in candidates}
    unavailable_count = 0
    budget_error: LlmBudgetExceeded | None = None

    for (symbol, analyst_name), res in zip(meta, results):
        if isinstance(res, LlmBudgetExceeded):
            budget_error = budget_error or res
            continue
        if isinstance(res, LlmValidationDropped):
            failures_by_symbol[symbol].append((analyst_name, "LlmValidationDropped"))
            continue
        if isinstance(res, LlmUnavailable):
            unavailable_count += 1
            failures_by_symbol[symbol].append((analyst_name, "LlmUnavailable"))
            continue
        if isinstance(res, Exception):
            logger.exception("analyst %s/%s raised an unexpected error", symbol, analyst_name, exc_info=res)
            failures_by_symbol[symbol].append((analyst_name, type(res).__name__))
            continue
        by_symbol[symbol][analyst_name] = res

    if budget_error is not None:
        raise budget_error

    if tasks and unavailable_count * 2 >= len(tasks):
        raise LlmUnavailable(f"{unavailable_count}/{len(tasks)} analyst calls unavailable -- degrading cycle to quant-only")

    out = []
    for c in candidates:
        symbol = c.snapshot.symbol
        bundle = EvidenceBundle(
            symbol=symbol, quant=c.snapshot, regime=c.decision,
            quant_analyst=by_symbol[symbol].get("QUANT"),  # type: ignore[arg-type]
            news_analyst=by_symbol[symbol].get("NEWS"),  # type: ignore[arg-type]
            sentiment_analyst=by_symbol[symbol].get("SENTIMENT"),  # type: ignore[arg-type]
            headlines=news.get(symbol, ()), mentions=mentions.get(symbol),
        )
        out.append(AnalystResult(symbol=symbol, bundle=bundle, failures=tuple(failures_by_symbol[symbol])))
    return out


def analyst_score(r: AnalystResult) -> float:
    """docs/day4_action_plan.md Step 1.4: 0.625*quant + 0.375*news, each in
    [0,1], measuring AGREEMENT WITH THE DETERMINISTIC STRUCTURE'S DIRECTION.
    Renormalised from the original 0.50/0.30/0.20 quant/news/sentiment split
    (5:3:2) after sentiment_analyst was retired -- 0.625/0.375 preserves the
    5:3 quant:news ratio exactly (both are 1.25x the old weights), so removing
    the constant 0.5 sentiment term changes no candidate's relative ranking.
    A missing analyst scores 0.5 (neutral) and its weight is NOT redistributed
    a second time, so a candidate is never advantaged by having fewer opinions
    among the two that remain."""
    structure = r.bundle.regime.structure
    direction = DIRECTION.get(structure, 0) if structure is not None else 0

    qa = r.bundle.quant_analyst
    if qa is None or direction == 0:
        quant_component = 0.5
    else:
        mom_sign = _MOMENTUM_SIGN[qa.directional_momentum]
        momentum_agree = 0.5 if mom_sign == 0 else (1.0 if mom_sign == direction else 0.0)

        is_credit = STRUCTURE_IS_CREDIT[structure]
        if qa.iv_rv_interpretation == "NEUTRAL":
            iv_agree = 0.5
        elif is_credit and qa.iv_rv_interpretation == "RICH":
            iv_agree = 1.0
        elif (not is_credit) and qa.iv_rv_interpretation == "CHEAP":
            iv_agree = 1.0
        else:
            iv_agree = 0.0
        quant_component = (momentum_agree + iv_agree) / 2.0

    news = r.bundle.news_analyst
    if news is None or direction == 0:
        news_component = 0.5
    else:
        impact_sign = _IMPACT_SIGN[news.expected_impact]
        news_component = 0.5 if impact_sign == 0 else (1.0 if impact_sign == direction else 0.0)

    return 0.625 * quant_component + 0.375 * news_component


def select_top(
    results: Sequence[AnalystResult], candidates: Sequence[ScreenedCandidate], n: int = DEBATE_CANDIDATES
) -> list[AnalystResult]:
    """Top `n` by analyst_score. Tie-break: Day-2 ScreenedCandidate.score, then
    UNIVERSE index -- the same deterministic ordering as ticker_screener.shortlist."""
    day2_score = {c.snapshot.symbol: c.score for c in candidates}
    ordered = sorted(results, key=lambda r: (-analyst_score(r), -day2_score[r.symbol], UNIVERSE.index(r.symbol)))
    return ordered[:n]

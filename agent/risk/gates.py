from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

from agent.config import (
    DAILY_LOSS_KILL_PCT,
    DRAWDOWN_CONSERVATIVE_PCT,
    DRAWDOWN_TERMINAL_PCT,
    DTE_MAX,
    DTE_MIN,
    EARNINGS_DATES,
    MAX_AGGREGATE_RISK_PCT,
    MAX_CONCURRENT_POSITIONS,
    MAX_POSITIONS_PER_UNDERLYING,
    MAX_RISK_PER_TRADE_PCT,
)
from agent.risk.greeks import PortfolioGreeks, marginal
from agent.risk.sizing import size_position
from agent.schemas.execution import STRUCTURE_IS_CREDIT, Intent, SpreadPlan

# `evaluate` takes no persona votes, no LLM output, and no confidence score --
# by construction (docs/day2_spine_plan.md Group 5). This module must never
# import agent.agents; a test guards that import graph.

_OPTION_SYMBOL_RE = re.compile(r"^[A-Z]{1,6}\d{6}[CP]\d{8}$")
_INTENT_FOR_SIDE: dict[str, Intent] = {"BUY": Intent.BUY_TO_OPEN, "SELL": Intent.SELL_TO_OPEN}


class GateReason(StrEnum):
    APPROVED = "APPROVED"
    EQUITY_ORDER_BLOCKED = "EQUITY_ORDER_BLOCKED"
    MALFORMED_LEG_COUNT = "MALFORMED_LEG_COUNT"
    MISSING_POSITION_INTENT = "MISSING_POSITION_INTENT"
    LIMIT_SIGN_MISMATCH = "LIMIT_SIGN_MISMATCH"
    STRIKE_NOT_IN_CHAIN = "STRIKE_NOT_IN_CHAIN"
    DRAWDOWN_TERMINAL = "DRAWDOWN_TERMINAL"
    DAILY_LOSS_KILL_SWITCH = "DAILY_LOSS_KILL_SWITCH"
    REDUCE_ONLY = "REDUCE_ONLY"
    CONSERVATIVE_MODE_CREDIT_BLOCKED = "CONSERVATIVE_MODE_CREDIT_BLOCKED"
    EARNINGS_BLACKOUT = "EARNINGS_BLACKOUT"
    DTE_OUT_OF_WINDOW = "DTE_OUT_OF_WINDOW"
    ENTRY_CUTOFF_PASSED = "ENTRY_CUTOFF_PASSED"
    MAX_CONCURRENT_POSITIONS = "MAX_CONCURRENT_POSITIONS"
    MAX_POSITIONS_PER_UNDERLYING = "MAX_POSITIONS_PER_UNDERLYING"
    NEGATIVE_EDGE = "NEGATIVE_EDGE"
    QTY_FLOORS_TO_ZERO = "QTY_FLOORS_TO_ZERO"
    LOW_CONVICTION = "LOW_CONVICTION"
    MAX_RISK_PER_TRADE = "MAX_RISK_PER_TRADE"
    MAX_AGGREGATE_RISK = "MAX_AGGREGATE_RISK"
    INSUFFICIENT_BUYING_POWER = "INSUFFICIENT_BUYING_POWER"
    PORTFOLIO_DELTA_LIMIT = "PORTFOLIO_DELTA_LIMIT"
    PORTFOLIO_VEGA_LIMIT = "PORTFOLIO_VEGA_LIMIT"
    LLM_BUDGET_CEILING = "LLM_BUDGET_CEILING"


@dataclass(frozen=True)
class GateContext:
    equity: Decimal
    buying_power: Decimal
    day_pnl_pct: float
    drawdown_pct: float                                # vs ACCOUNT_START_EQUITY
    open_position_keys: frozenset[tuple[str, date]]    # (underlying, expiry)
    open_underlyings: frozenset[str]
    aggregate_defined_risk: Decimal
    portfolio: PortfolioGreeks
    session_date: date
    past_entry_cutoff: bool
    reduce_only: bool
    chain_symbols: frozenset[str]                      # OCC keys of this underlying's live chain
    earnings_armed: bool
    # Day 3 (docs/day3_llm_plan.md S1b): appended LAST so every Day-2 GateContext(...)
    # call site keeps constructing positionally without change.
    llm_budget_exhausted: bool = False
    # Day 4 (docs/day4_track_ab_plan.md §2.4): the debate's conviction, [0.0,
    # 1.0]. Same trailing-default convention as llm_budget_exhausted above.
    conviction: float = 1.0


@dataclass(frozen=True)
class GateDecision:
    approved: bool
    reason: GateReason
    qty: int
    detail: str
    observed_value: float | None
    threshold_value: float | None


def _reject(
    reason: GateReason, observed: float | None = None, threshold: float | None = None
) -> GateDecision:
    return GateDecision(
        approved=False, reason=reason, qty=0, detail=reason.value,
        observed_value=observed, threshold_value=threshold,
    )


def _solve_cap(current: float, marginal_per_spread: float, limit: float) -> int:
    """Largest integer q >= 0 satisfying |current + q*marginal_per_spread| <= limit.
    A naive q <= (limit - current)/marginal produces a negative bound whenever
    marginal is negative (a risk-REDUCING trade) -- solved properly here so a
    hedging trade is never killed by its own cap. marginal == 0 is unbounded."""
    if marginal_per_spread == 0:
        return sys.maxsize
    if marginal_per_spread > 0:
        bound = (limit - current) / marginal_per_spread
    else:
        bound = (-limit - current) / marginal_per_spread
    return max(0, int(bound))


def evaluate(plan: SpreadPlan, ctx: GateContext) -> GateDecision:
    # Phase A -- structural (cheapest, most absolute).
    if any(_OPTION_SYMBOL_RE.match(leg.occ_symbol) is None for leg in plan.legs):
        return _reject(GateReason.EQUITY_ORDER_BLOCKED)
    if not 2 <= len(plan.legs) <= 4:
        return _reject(GateReason.MALFORMED_LEG_COUNT)
    if any(leg.intent != _INTENT_FOR_SIDE[leg.side] for leg in plan.legs):
        return _reject(GateReason.MISSING_POSITION_INTENT)
    if STRUCTURE_IS_CREDIT[plan.structure] != (plan.net_mid < 0):
        return _reject(GateReason.LIMIT_SIGN_MISMATCH)
    if any(leg.occ_symbol not in ctx.chain_symbols for leg in plan.legs):
        return _reject(GateReason.STRIKE_NOT_IN_CHAIN)

    # Phase B -- account state.
    if ctx.drawdown_pct <= DRAWDOWN_TERMINAL_PCT:
        return _reject(GateReason.DRAWDOWN_TERMINAL, ctx.drawdown_pct, DRAWDOWN_TERMINAL_PCT)
    if ctx.day_pnl_pct <= DAILY_LOSS_KILL_PCT:
        return _reject(GateReason.DAILY_LOSS_KILL_SWITCH, ctx.day_pnl_pct, DAILY_LOSS_KILL_PCT)
    if ctx.reduce_only:
        return _reject(GateReason.REDUCE_ONLY)
    if ctx.llm_budget_exhausted:
        return _reject(GateReason.LLM_BUDGET_CEILING)
    conservative_mode = ctx.drawdown_pct <= DRAWDOWN_CONSERVATIVE_PCT
    if conservative_mode and STRUCTURE_IS_CREDIT[plan.structure]:
        return _reject(GateReason.CONSERVATIVE_MODE_CREDIT_BLOCKED, ctx.drawdown_pct, DRAWDOWN_CONSERVATIVE_PCT)

    # Phase C -- candidate eligibility.
    if ctx.past_entry_cutoff:
        return _reject(GateReason.ENTRY_CUTOFF_PASSED)
    if not DTE_MIN <= plan.dte <= DTE_MAX:
        return _reject(GateReason.DTE_OUT_OF_WINDOW, float(plan.dte), float(DTE_MIN))
    earnings_date = EARNINGS_DATES.get(plan.symbol)
    if ctx.earnings_armed and earnings_date is not None and ctx.session_date <= earnings_date <= plan.expiry:
        return _reject(GateReason.EARNINGS_BLACKOUT)
    if len(ctx.open_position_keys) >= MAX_CONCURRENT_POSITIONS:
        return _reject(
            GateReason.MAX_CONCURRENT_POSITIONS,
            float(len(ctx.open_position_keys)),
            float(MAX_CONCURRENT_POSITIONS),
        )
    if plan.symbol in ctx.open_underlyings:
        return _reject(GateReason.MAX_POSITIONS_PER_UNDERLYING)

    # Phase D -- sizing, as a minimum over independent, monotone-non-increasing
    # caps (O(1), and the argmin is exactly "the specific numeric threshold
    # that decided it").
    sized = size_position(plan, ctx.equity)
    if sized.reason == "NEGATIVE_EDGE":
        return _reject(GateReason.NEGATIVE_EDGE, sized.kelly_fraction, 0.0)

    q = sized.qty
    if conservative_mode:
        q //= 2  # halve size; credit structures were already blocked above
    pre_conviction_q = q
    q = int(q * ctx.conviction)   # [0,1] -- can only ever reduce. Never applied to `cap`.

    marginal_delta, marginal_vega = marginal(plan, 1)
    per_contract_risk = float(plan.max_loss_per_spread)
    per_contract_bp = plan.width * 100.0

    caps: dict[GateReason, int] = {
        GateReason.MAX_RISK_PER_TRADE: int((MAX_RISK_PER_TRADE_PCT * float(ctx.equity)) // per_contract_risk),
        GateReason.MAX_AGGREGATE_RISK: max(
            0,
            int(
                ((MAX_AGGREGATE_RISK_PCT * float(ctx.equity)) - float(ctx.aggregate_defined_risk))
                // per_contract_risk
            ),
        ),
        GateReason.INSUFFICIENT_BUYING_POWER: int(float(ctx.buying_power) // per_contract_bp),
        GateReason.PORTFOLIO_DELTA_LIMIT: _solve_cap(
            ctx.portfolio.delta_dollars, marginal_delta, ctx.portfolio.delta_limit
        ),
        GateReason.PORTFOLIO_VEGA_LIMIT: _solve_cap(
            ctx.portfolio.vega_dollars, marginal_vega, ctx.portfolio.vega_limit
        ),
    }
    binding, cap = min(caps.items(), key=lambda kv: kv[1])
    q_final = min(q, cap)

    if q_final < 1:
        if cap >= 1:
            # Every independent cap allows at least one contract. If Kelly
            # sizing itself already floored to zero pre-conviction, that's
            # QTY_FLOORS_TO_ZERO as before; if sizing cleared >=1 and the
            # conviction multiplier is what took it to zero, say so instead
            # of reporting a misleading QTY_FLOORS_TO_ZERO.
            if pre_conviction_q >= 1:
                return _reject(GateReason.LOW_CONVICTION, ctx.conviction, 1.0)
            return _reject(GateReason.QTY_FLOORS_TO_ZERO, sized.kelly_fraction, 0.0)
        observed = {
            GateReason.MAX_RISK_PER_TRADE: per_contract_risk,
            GateReason.MAX_AGGREGATE_RISK: float(ctx.aggregate_defined_risk) + per_contract_risk,
            GateReason.INSUFFICIENT_BUYING_POWER: per_contract_bp,
            GateReason.PORTFOLIO_DELTA_LIMIT: abs(ctx.portfolio.delta_dollars + marginal_delta),
            GateReason.PORTFOLIO_VEGA_LIMIT: abs(ctx.portfolio.vega_dollars + marginal_vega),
        }[binding]
        threshold = {
            GateReason.MAX_RISK_PER_TRADE: MAX_RISK_PER_TRADE_PCT * float(ctx.equity),
            GateReason.MAX_AGGREGATE_RISK: MAX_AGGREGATE_RISK_PCT * float(ctx.equity),
            GateReason.INSUFFICIENT_BUYING_POWER: float(ctx.buying_power),
            GateReason.PORTFOLIO_DELTA_LIMIT: ctx.portfolio.delta_limit,
            GateReason.PORTFOLIO_VEGA_LIMIT: ctx.portfolio.vega_limit,
        }[binding]
        return _reject(binding, observed, threshold)

    return GateDecision(
        approved=True, reason=GateReason.APPROVED, qty=q_final, detail=GateReason.APPROVED.value,
        observed_value=None, threshold_value=None,
    )

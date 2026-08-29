from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from agent.agents.evidence import EvidenceBundle
from agent.agents.prompts import RISK_AGGRESSIVE_SYSTEM, RISK_CONSERVATIVE_SYSTEM, RISK_NEUTRAL_SYSTEM
from agent.config import MAX_RISK_PER_TRADE_PCT
from agent.schemas.execution import SpreadPlan
from agent.schemas.llm import RiskManagerOutput
from agent.tools.llm import LlmBudgetExceeded, LlmPort, LlmUnavailable, LlmValidationDropped

logger = logging.getLogger(__name__)


class AccountView(Protocol):
    """Structural, satisfied by execution.cli_bridge.CliAccount without an
    import -- agent/agents/* may not import agent.execution
    (docs/day3_llm_plan.md S0.2, test_agents_never_execute)."""

    equity: Decimal
    last_equity: Decimal
    buying_power: Decimal


class PortfolioView(Protocol):
    """Structural, satisfied by risk.greeks.PortfolioGreeks without an import
    -- agent/agents/* may not import agent.risk (same test)."""

    delta_dollars: float
    vega_dollars: float
    delta_limit: float
    vega_limit: float
    position_keys: frozenset


_PERSONAS: tuple[tuple[str, str], ...] = (
    ("AGGRESSIVE", RISK_AGGRESSIVE_SYSTEM),
    ("NEUTRAL", RISK_NEUTRAL_SYSTEM),
    ("CONSERVATIVE", RISK_CONSERVATIVE_SYSTEM),
)


@dataclass(frozen=True)
class RiskTeamResult:
    votes: tuple[RiskManagerOutput, ...]          # 0-3; a dropped persona is simply absent
    vetoed: bool
    veto_reason: str | None


def _plan_summary(plan: SpreadPlan) -> dict:
    return {
        "symbol": plan.symbol, "structure": plan.structure.value, "expiry": plan.expiry.isoformat(),
        "dte": plan.dte, "width": round(plan.width, 2), "net_mid": str(plan.net_mid),
        "max_profit_per_spread": str(plan.max_profit_per_spread),
        "max_loss_per_spread": str(plan.max_loss_per_spread), "p_success": round(plan.p_success, 3),
    }


def _account_summary(account: AccountView, portfolio: PortfolioView) -> dict:
    day_pnl_pct = float((account.equity - account.last_equity) / account.last_equity) if account.last_equity else 0.0
    return {
        "equity": str(account.equity), "buying_power": str(account.buying_power),
        "day_pnl_pct": round(day_pnl_pct, 4), "open_positions": len(portfolio.position_keys),
        "portfolio_delta_dollars": round(portfolio.delta_dollars, 2),
        "portfolio_vega_dollars": round(portfolio.vega_dollars, 2),
        "portfolio_delta_limit": round(portfolio.delta_limit, 2),
        "portfolio_vega_limit": round(portfolio.vega_limit, 2),
    }


def _risk_prompt(plan: SpreadPlan, bundle: EvidenceBundle, account: AccountView, portfolio: PortfolioView) -> str:
    max_loss_pct_of_equity = float(plan.max_loss_per_spread) / float(account.equity) if account.equity else 0.0
    return (
        f"Proposed trade: {json.dumps(_plan_summary(plan), separators=(',', ':'))}\n"
        f"Max loss as fraction of equity for ONE spread: {max_loss_pct_of_equity:.4f} "
        f"(informational only -- the deterministic gate enforces the {MAX_RISK_PER_TRADE_PCT:.3f} "
        f"cap and the actual position size regardless of your vote)\n"
        f"Account/portfolio state: {json.dumps(_account_summary(account, portfolio), separators=(',', ':'))}\n"
        f"Evidence: {bundle.to_prompt_json()}"
    )


async def run_risk_team(
    llm: LlmPort, plan: SpreadPlan, bundle: EvidenceBundle, account: AccountView, portfolio: PortfolioView,
    *, sem: asyncio.Semaphore, sink: list[int],
) -> RiskTeamResult:
    """3 parallel calls, one per persona, same context. Never raises: a dropped
    persona is simply absent from `votes` (docs/day3_llm_plan.md Group 5).

    Personas can tighten, never loosen -- enforced by construction, not by
    convention: RESIZE votes are logged here and nowhere applied (sizing is
    half-Kelly plus the five deterministic gate caps, full stop -- neither
    this function nor its caller ever reads a vote's `decision` to change
    qty), and APPROVE does nothing but count toward `votes` -- it cannot
    relax a gate or resurrect a rejected plan. Only REJECT has any effect at
    all, and only through the veto count below."""
    prompt = _risk_prompt(plan, bundle, account, portfolio)

    async def _vote(persona: str, system: str) -> RiskManagerOutput | None:
        async with sem:
            try:
                return await llm.complete_json(
                    prompt, RiskManagerOutput, node=f"RISK_{persona}", system=system, sink=sink
                )
            except (LlmValidationDropped, LlmUnavailable, LlmBudgetExceeded) as e:
                logger.warning("risk persona %s dropped: %s", persona, e)
                return None
            except Exception:
                logger.exception("risk persona %s raised an unexpected error", persona)
                return None

    results = await asyncio.gather(*(_vote(persona, system) for persona, system in _PERSONAS))
    votes = tuple(v for v in results if v is not None)

    # The veto rule, stated as code (docs/day3_llm_plan.md Group 5): two of
    # three REJECT votes veto the candidate. Nothing else in this module or
    # its caller reads `decision` for any other purpose.
    vetoed = sum(v.decision == "REJECT" for v in votes) >= 2
    veto_reason = "RISK_TEAM_VETO" if vetoed else None
    return RiskTeamResult(votes=votes, vetoed=vetoed, veto_reason=veto_reason)

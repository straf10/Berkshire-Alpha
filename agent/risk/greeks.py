from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Mapping, Sequence

from agent.config import PORTFOLIO_DELTA_PCT, PORTFOLIO_VEGA_PCT
from agent.execution.alpaca_client import AlpacaClients
from agent.execution.cli_bridge import CliPosition
from agent.schemas.execution import SpreadPlan
from agent.tools.market_data import fetch_leg_snapshots


@dataclass(frozen=True)
class LegExposure:
    occ_symbol: str
    underlying: str
    expiry: date
    qty: int                       # SIGNED: +n long, -n short
    delta: float
    vega: float
    spot: float


@dataclass(frozen=True)
class PortfolioGreeks:
    delta_dollars: float
    vega_dollars: float
    delta_limit: float
    vega_limit: float
    delta_breached: bool
    vega_breached: bool
    largest_delta_contributor: str | None
    largest_vega_contributor: str | None
    position_keys: frozenset[tuple[str, date]]


async def build_exposures(
    positions: Sequence[CliPosition], clients: AlpacaClients, spots: Mapping[str, float]
) -> list[LegExposure]:
    """ONE batched fetch_leg_snapshots() call for every held option contract."""
    option_positions = [p for p in positions if p.asset_class == "us_option"]
    occ_symbols = [p.symbol for p in option_positions]
    snapshots = await fetch_leg_snapshots(clients, occ_symbols)

    exposures: list[LegExposure] = []
    for p in option_positions:
        q = snapshots.get(p.symbol)
        if q is None:
            continue
        exposures.append(
            LegExposure(
                occ_symbol=p.symbol,
                underlying=q.underlying,
                expiry=q.expiry,
                qty=int(p.qty),
                delta=q.delta,
                vega=q.vega,
                spot=spots.get(q.underlying, 0.0),
            )
        )
    return exposures


def aggregate(exposures: Sequence[LegExposure], equity: Decimal) -> PortfolioGreeks:
    """Delta is multiplied by the underlying price; vega is not.
    Delta_P = Sum (delta_leg * qty_leg) * S_underlying * 100 <= PORTFOLIO_DELTA_PCT * Equity
    Vega_P  = Sum (vega_leg  * qty_leg) * 100               <= PORTFOLIO_VEGA_PCT  * Equity
    Both limits are absolute-value tests."""
    delta_dollars = sum(e.delta * e.qty * e.spot * 100.0 for e in exposures)
    vega_dollars = sum(e.vega * e.qty * 100.0 for e in exposures)

    delta_limit = PORTFOLIO_DELTA_PCT * float(equity)
    vega_limit = PORTFOLIO_VEGA_PCT * float(equity)

    largest_delta = max(exposures, key=lambda e: abs(e.delta * e.qty * e.spot * 100.0), default=None)
    largest_vega = max(exposures, key=lambda e: abs(e.vega * e.qty * 100.0), default=None)

    return PortfolioGreeks(
        delta_dollars=delta_dollars,
        vega_dollars=vega_dollars,
        delta_limit=delta_limit,
        vega_limit=vega_limit,
        delta_breached=abs(delta_dollars) > delta_limit,
        vega_breached=abs(vega_dollars) > vega_limit,
        largest_delta_contributor=largest_delta.occ_symbol if largest_delta is not None else None,
        largest_vega_contributor=largest_vega.occ_symbol if largest_vega is not None else None,
        position_keys=frozenset((e.underlying, e.expiry) for e in exposures),
    )


def marginal(plan: SpreadPlan, qty: int) -> tuple[float, float]:
    """(Delta$, Vega$) that submitting `qty` spreads of `plan` would add.
    sign(BUY)=+1, sign(SELL)=-1 -- matches Alpaca's already-signed leg deltas
    (a short put's delta is negative, so selling it contributes positive
    portfolio delta for a bull put spread)."""
    delta_dollars = 0.0
    vega_dollars = 0.0
    for leg in plan.legs:
        sign = 1 if leg.side == "BUY" else -1
        signed_qty = sign * leg.ratio_qty * qty
        delta_dollars += leg.delta * signed_qty * plan.spot * 100.0
        vega_dollars += leg.vega * signed_qty * 100.0
    return delta_dollars, vega_dollars

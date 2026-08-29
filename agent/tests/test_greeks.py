from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from agent.execution.cli_bridge import CliPosition
from agent.risk.greeks import LegExposure, aggregate, build_exposures, marginal
from agent.schemas.execution import Intent, Leg, Regime, SpreadPlan, Structure

EXPIRY = date(2026, 9, 4)


def _exposure(occ: str, underlying: str, qty: int, delta: float, vega: float, spot: float) -> LegExposure:
    return LegExposure(occ_symbol=occ, underlying=underlying, expiry=EXPIRY, qty=qty, delta=delta, vega=vega, spot=spot)


def _leg(side: str, delta: float, vega: float = 0.05) -> Leg:
    intent = Intent.SELL_TO_OPEN if side == "SELL" else Intent.BUY_TO_OPEN
    strike = 100.0 if side == "SELL" else 97.0
    return Leg(
        occ_symbol=f"TST260904P{int(strike*1000):08d}", strike=strike, right="P", side=side,
        ratio_qty=1, intent=intent, delta=delta, vega=vega, bid=1.0, ask=1.1,
    )


def _bull_put_plan(spot: float = 100.0) -> SpreadPlan:
    return SpreadPlan(
        symbol="TST", structure=Structure.BULL_PUT_SPREAD, regime=Regime.CREDIT, expiry=EXPIRY, dte=4,
        legs=(_leg("SELL", -0.28), _leg("BUY", -0.10)),
        width=3.0, net_mid=Decimal("-0.90"), net_natural=Decimal("-0.75"),
        max_profit_per_spread=Decimal("90"), max_loss_per_spread=Decimal("210"),
        p_success=0.72, spot=spot, short_leg_delta=0.28,
    )


def test_short_put_contributes_positive_delta() -> None:
    plan = _bull_put_plan()
    delta_d, _ = marginal(plan, 1)
    assert delta_d > 0


def test_delta_uses_spot_vega_does_not() -> None:
    exposures_100 = [_exposure("A", "TST", -1, -0.28, 0.05, 100.0)]
    exposures_500 = [_exposure("A", "TST", -1, -0.28, 0.05, 500.0)]
    g100 = aggregate(exposures_100, Decimal("100000"))
    g500 = aggregate(exposures_500, Decimal("100000"))
    assert g500.delta_dollars == pytest.approx(g100.delta_dollars * 5)
    assert g500.vega_dollars == pytest.approx(g100.vega_dollars)


def test_limits_scale_with_equity() -> None:
    g = aggregate([], Decimal("100000"))
    assert g.delta_limit == 15000.0
    assert g.vega_limit == 2000.0


def test_breach_is_absolute_value() -> None:
    # qty=-2, delta=1.0, spot=100 -> delta_dollars = -2*1.0*100*100 = -20000
    exposures = [_exposure("A", "TST", -2, 1.0, 0.0, 100.0)]
    g = aggregate(exposures, Decimal("100000"))
    assert g.delta_dollars == pytest.approx(-20000.0)
    assert g.delta_breached is True


def test_position_count_groups_legs() -> None:
    exposures = []
    for i in range(6):
        exposures.append(_exposure(f"SHORT{i}", f"SYM{i}", -1, -0.28, 0.05, 100.0))
        exposures.append(_exposure(f"LONG{i}", f"SYM{i}", 1, -0.10, 0.03, 100.0))
    g = aggregate(exposures, Decimal("100000"))
    assert len(g.position_keys) == 6


async def test_leg_snapshots_batched() -> None:
    calls = []

    class _Greeks:
        delta, gamma, theta, vega = -0.28, 0.01, -0.01, 0.05

    class _Quote:
        bid_price, ask_price = 1.0, 1.1

    class _Snap:
        implied_volatility = 0.2
        greeks = _Greeks()
        latest_quote = _Quote()

    class FakeClients:
        async def get_option_snapshot(self, req):
            calls.append(req)
            return {occ: _Snap() for occ in req.symbol_or_symbols}

    positions = [
        CliPosition(
            symbol=f"TST260904P{100000 + i * 1000:08d}", asset_class="us_option", qty=Decimal(-1),
            avg_entry_price=Decimal("1.0"), market_value=Decimal("-100"), unrealized_pl=Decimal("0"),
        )
        for i in range(12)
    ]
    exposures = await build_exposures(positions, FakeClients(), {"TST": 100.0})
    assert len(calls) == 1
    assert len(calls[0].symbol_or_symbols) == 12
    assert len(exposures) == 12

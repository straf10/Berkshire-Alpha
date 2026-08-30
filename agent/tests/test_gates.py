from __future__ import annotations

import inspect
from datetime import date
from decimal import Decimal

import pytest

from agent.risk.gates import GateContext, GateReason, evaluate
from agent.risk.greeks import PortfolioGreeks
from agent.schemas.execution import Intent, Leg, Regime, SpreadPlan, Structure

EXPIRY = date(2026, 9, 4)
SESSION_DATE = date(2026, 8, 31)


def _leg(side: str, strike: float, delta: float, intent: Intent | None = None, occ: str | None = None) -> Leg:
    if intent is None:
        intent = Intent.SELL_TO_OPEN if side == "SELL" else Intent.BUY_TO_OPEN
    if occ is None:
        occ = f"TST260904P{int(strike*1000):08d}"
    return Leg(occ_symbol=occ, strike=strike, right="P", side=side, ratio_qty=1, intent=intent,
               delta=delta, vega=0.05, bid=1.0, ask=1.1)


def _plan(
    *, structure: Structure = Structure.BULL_PUT_SPREAD, legs=None, net_mid=Decimal("-0.90"),
    max_profit=Decimal("90"), max_loss=Decimal("210"), dte=4, expiry=EXPIRY, p_success=0.72,
    symbol="TST", width=3.0,
) -> SpreadPlan:
    if legs is None:
        legs = (_leg("SELL", 100.0, -0.28), _leg("BUY", 97.0, -0.10))
    return SpreadPlan(
        symbol=symbol, structure=structure, regime=Regime.CREDIT, expiry=expiry, dte=dte,
        legs=legs, width=width, net_mid=net_mid, net_natural=Decimal("-0.75"),
        max_profit_per_spread=max_profit, max_loss_per_spread=max_loss,
        p_success=p_success, spot=100.0, short_leg_delta=0.28,
    )


def _portfolio(delta_dollars: float = 0.0, vega_dollars: float = 0.0, equity: Decimal = Decimal("100000")) -> PortfolioGreeks:
    return PortfolioGreeks(
        delta_dollars=delta_dollars, vega_dollars=vega_dollars,
        delta_limit=0.15 * float(equity), vega_limit=0.02 * float(equity),
        delta_breached=abs(delta_dollars) > 0.15 * float(equity),
        vega_breached=abs(vega_dollars) > 0.02 * float(equity),
        largest_delta_contributor=None, largest_vega_contributor=None, position_keys=frozenset(),
    )


def _ctx(**overrides) -> GateContext:
    base = dict(
        equity=Decimal("100000"), buying_power=Decimal("50000"), day_pnl_pct=0.0, drawdown_pct=0.0,
        open_position_keys=frozenset(), open_underlyings=frozenset(), aggregate_defined_risk=Decimal("0"),
        portfolio=_portfolio(), session_date=SESSION_DATE, past_entry_cutoff=False, reduce_only=False,
        chain_symbols=frozenset({leg.occ_symbol for leg in (_leg("SELL", 100.0, -0.28), _leg("BUY", 97.0, -0.10))}),
        earnings_armed=False,
    )
    base.update(overrides)
    return GateContext(**base)


def test_gate_signature_accepts_no_votes() -> None:
    sig = inspect.signature(evaluate)
    assert list(sig.parameters) == ["plan", "ctx"]
    for field_name in GateContext.__dataclass_fields__:
        assert "persona" not in field_name and "vote" not in field_name and "confidence" not in field_name


def test_gate_imports_no_agents() -> None:
    import ast
    from pathlib import Path

    src = Path("agent/risk/gates.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert not node.module.startswith("agent.agents")
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("agent.agents")


def test_gate_is_pure() -> None:
    plan = _plan()
    ctx = _ctx()
    assert evaluate(plan, ctx) == evaluate(plan, ctx)


def test_oversized_trade_rejected() -> None:
    # threshold is 2000.0 (2% of $100k) -- docs/day4_track_ab_plan.md §0.4
    # raised MAX_RISK_PER_TRADE_PCT 1.5% -> 2%.
    plan = _plan(max_loss=Decimal("5000"), max_profit=Decimal("2000"))
    decision = evaluate(plan, _ctx())
    assert not decision.approved
    assert decision.reason == GateReason.MAX_RISK_PER_TRADE
    assert decision.threshold_value == 2000.0


def test_credit_plan_with_positive_mid_rejected() -> None:
    plan = _plan(structure=Structure.BULL_PUT_SPREAD, net_mid=Decimal("0.90"))
    decision = evaluate(plan, _ctx())
    assert decision.reason == GateReason.LIMIT_SIGN_MISMATCH


def test_debit_plan_with_negative_mid_rejected() -> None:
    plan = _plan(structure=Structure.BULL_CALL_SPREAD, net_mid=Decimal("-2.06"))
    decision = evaluate(plan, _ctx())
    assert decision.reason == GateReason.LIMIT_SIGN_MISMATCH


def test_five_leg_plan_rejected() -> None:
    legs = tuple(_leg("SELL", 100.0 + i, -0.28, occ=f"TST260904P{int((100+i)*1000):08d}") for i in range(5))
    plan = _plan(legs=legs)
    decision = evaluate(plan, _ctx(chain_symbols=frozenset(leg.occ_symbol for leg in legs)))
    assert decision.reason == GateReason.MALFORMED_LEG_COUNT


def test_closing_intent_on_open_rejected() -> None:
    legs = (_leg("SELL", 100.0, -0.28, intent=Intent.SELL_TO_CLOSE), _leg("BUY", 97.0, -0.10))
    plan = _plan(legs=legs)
    decision = evaluate(plan, _ctx(chain_symbols=frozenset(leg.occ_symbol for leg in legs)))
    assert decision.reason == GateReason.MISSING_POSITION_INTENT


def test_strike_not_in_chain_rejected() -> None:
    plan = _plan()
    decision = evaluate(plan, _ctx(chain_symbols=frozenset({plan.legs[0].occ_symbol})))
    assert decision.reason == GateReason.STRIKE_NOT_IN_CHAIN


def test_equity_leg_blocked() -> None:
    legs = (_leg("SELL", 100.0, -0.28, occ="AAPL"), _leg("BUY", 97.0, -0.10))
    plan = _plan(legs=legs)
    decision = evaluate(plan, _ctx(chain_symbols=frozenset(leg.occ_symbol for leg in legs)))
    assert decision.reason == GateReason.EQUITY_ORDER_BLOCKED


def test_entry_cutoff_blocks() -> None:
    plan = _plan()
    decision = evaluate(plan, _ctx(past_entry_cutoff=True))
    assert decision.reason == GateReason.ENTRY_CUTOFF_PASSED


def test_dte_window() -> None:
    plan_low = _plan(dte=2)
    plan_high = _plan(dte=8)
    assert evaluate(plan_low, _ctx()).reason == GateReason.DTE_OUT_OF_WINDOW
    assert evaluate(plan_high, _ctx()).reason == GateReason.DTE_OUT_OF_WINDOW
    for dte in (3, 5, 7):
        decision = evaluate(_plan(dte=dte), _ctx())
        assert decision.reason != GateReason.DTE_OUT_OF_WINDOW


def test_earnings_blackout(monkeypatch: pytest.MonkeyPatch) -> None:
    import agent.risk.gates as gates_module

    monkeypatch.setattr(gates_module, "EARNINGS_DATES", {"TST": date(2026, 9, 2)})
    plan = _plan(expiry=EXPIRY, dte=4)
    decision = evaluate(plan, _ctx(earnings_armed=True, session_date=SESSION_DATE))
    assert decision.reason == GateReason.EARNINGS_BLACKOUT

    monkeypatch.setattr(gates_module, "EARNINGS_DATES", {"TST": date(2026, 9, 10)})
    decision = evaluate(plan, _ctx(earnings_armed=True, session_date=SESSION_DATE))
    assert decision.reason != GateReason.EARNINGS_BLACKOUT


def test_earnings_gate_disarmed_when_unverified(monkeypatch: pytest.MonkeyPatch) -> None:
    import agent.risk.gates as gates_module

    monkeypatch.setattr(gates_module, "EARNINGS_DATES", {"TST": date(2026, 9, 2)})
    plan = _plan()
    decision = evaluate(plan, _ctx(earnings_armed=False))
    assert decision.reason != GateReason.EARNINGS_BLACKOUT


def test_max_concurrent_positions() -> None:
    keys = frozenset((f"SYM{i}", EXPIRY) for i in range(6))
    plan = _plan()
    decision = evaluate(plan, _ctx(open_position_keys=keys))
    assert decision.reason == GateReason.MAX_CONCURRENT_POSITIONS


def test_max_per_underlying() -> None:
    plan = _plan(symbol="TST")
    decision = evaluate(plan, _ctx(open_underlyings=frozenset({"TST"})))
    assert decision.reason == GateReason.MAX_POSITIONS_PER_UNDERLYING


def test_daily_kill_switch() -> None:
    # Boundary is -5% now -- docs/day4_track_ab_plan.md §0.4 (Correction 4,
    # coupled with the 2% per-trade cap so two bad trades don't trip it).
    plan = _plan()
    assert evaluate(plan, _ctx(day_pnl_pct=-0.051)).reason == GateReason.DAILY_LOSS_KILL_SWITCH
    assert evaluate(plan, _ctx(day_pnl_pct=-0.049)).approved


def test_drawdown_conservative_halves_and_blocks_credit() -> None:
    credit_plan = _plan(structure=Structure.BULL_PUT_SPREAD)
    decision = evaluate(credit_plan, _ctx(drawdown_pct=-0.09))
    assert decision.reason == GateReason.CONSERVATIVE_MODE_CREDIT_BLOCKED

    debit_legs = (_leg("BUY", 100.0, 0.50, occ="TST260904C00100000"),
                  _leg("SELL", 105.0, 0.30, occ="TST260904C00105000"))
    # max_profit/max_loss sized so sizing's own MAX_RISK_PER_TRADE cap (now 2%
    # of equity, docs/day4_track_ab_plan.md §0.4) stays the binding constraint
    # in both branches below -- at the old 1.5% cap this pair coincidentally
    # tied with the portfolio delta cap at exactly 7, which is what let the
    # un-halved "// 2" assertion pass; the tie no longer holds at 2%.
    debit_plan = SpreadPlan(
        symbol="TST", structure=Structure.BULL_CALL_SPREAD, regime=Regime.DEBIT, expiry=EXPIRY, dte=4,
        legs=debit_legs, width=5.0, net_mid=Decimal("2.06"), net_natural=Decimal("2.40"),
        max_profit_per_spread=Decimal("428"), max_loss_per_spread=Decimal("300"),
        p_success=0.50, spot=100.0, short_leg_delta=0.30,
    )
    ctx = _ctx(drawdown_pct=-0.09, chain_symbols=frozenset(leg.occ_symbol for leg in debit_legs))
    baseline = evaluate(debit_plan, _ctx(chain_symbols=frozenset(leg.occ_symbol for leg in debit_legs)))
    halved = evaluate(debit_plan, ctx)
    assert baseline.approved and halved.approved
    assert halved.qty == baseline.qty // 2


def test_drawdown_terminal() -> None:
    from agent.schemas.execution import STRUCTURE_IS_CREDIT

    for structure in Structure:
        net_mid = Decimal("-0.90") if STRUCTURE_IS_CREDIT[structure] else Decimal("0.90")
        plan = _plan(structure=structure, net_mid=net_mid)
        decision = evaluate(plan, _ctx(drawdown_pct=-0.13))
        assert decision.reason == GateReason.DRAWDOWN_TERMINAL


def test_aggregate_risk_cap() -> None:
    # Aggregate cap = floor((0.10*100000 - 9600) / 100) = 4; sizing's own cap
    # (floor(2000/100) = 20) is looser, so the aggregate cap must bind.
    # docs/day4_track_ab_plan.md §0.4 raised MAX_AGGREGATE_RISK_PCT 8% -> 10%
    # (coupled with MAX_RISK_PER_TRADE_PCT's 1.5% -> 2% rise).
    plan = _plan(max_loss=Decimal("100"), max_profit=Decimal("40"), p_success=0.95)
    decision = evaluate(plan, _ctx(aggregate_defined_risk=Decimal("9600")))
    assert decision.approved
    assert decision.qty == 4
    total_after = 9600 + float(plan.max_loss_per_spread) * decision.qty
    assert total_after <= 10000.0 + 1e-9


def test_aggregate_cap_admits_five_positions() -> None:
    # docs/day4_track_ab_plan.md F2: at 2%/trade ($2000 max_loss) and a 10%
    # aggregate ceiling ($10000), the book saturates at exactly 5 positions --
    # slot 5 is admissible, slot 6 is not (deliberate: raising the ceiling to
    # make slot 6 reachable would put 12% of equity at defined risk against
    # an 8% conservative-mode brake).
    plan = _plan(max_loss=Decimal("2000"), max_profit=Decimal("1000"), p_success=0.95)
    fifth = evaluate(plan, _ctx(aggregate_defined_risk=Decimal("8000")))
    assert fifth.approved
    assert fifth.qty == 1

    sixth = evaluate(plan, _ctx(aggregate_defined_risk=Decimal("10000")))
    assert not sixth.approved
    assert sixth.reason == GateReason.MAX_AGGREGATE_RISK


def test_buying_power_cap() -> None:
    plan = _plan(width=3.0, max_loss=Decimal("210"), max_profit=Decimal("90"), p_success=0.95)
    decision = evaluate(plan, _ctx(buying_power=Decimal("900")))
    assert decision.approved
    assert decision.qty <= 3


def test_delta_cap_resizes() -> None:
    legs = (_leg("SELL", 100.0, -0.28), _leg("BUY", 97.0, -0.10))
    plan = _plan(legs=legs, max_loss=Decimal("21"), max_profit=Decimal("9"), p_success=0.95)
    portfolio = _portfolio(delta_dollars=14000.0)
    ctx = _ctx(portfolio=portfolio, chain_symbols=frozenset(leg.occ_symbol for leg in legs))
    decision = evaluate(plan, ctx)
    from agent.risk.greeks import marginal as marginal_fn
    m_delta, _ = marginal_fn(plan, 1)
    assert abs(14000.0 + m_delta * decision.qty) <= 15000.0 + 1e-6


def test_delta_cap_allows_hedging_trade() -> None:
    """A book at Delta$ +14,000 and a plan whose marginal delta is NEGATIVE
    must not be resized by the delta cap -- guards the sign bug where a naive
    q <= (limit - current)/marginal returns a negative bound."""
    legs = (_leg("BUY", 100.0, 0.28, occ="TST260904C00100000"), _leg("SELL", 105.0, 0.10, occ="TST260904C00105000"))
    plan = SpreadPlan(
        symbol="TST", structure=Structure.BEAR_CALL_SPREAD, regime=Regime.CREDIT, expiry=EXPIRY, dte=4,
        legs=(_leg("SELL", 100.0, -0.28, occ="TST260904P00100000"), _leg("BUY", 97.0, -0.10, occ="TST260904P00097000")),
        width=3.0, net_mid=Decimal("-0.90"), net_natural=Decimal("-0.75"),
        max_profit_per_spread=Decimal("90"), max_loss_per_spread=Decimal("210"),
        p_success=0.72, spot=100.0, short_leg_delta=0.28,
    )
    from agent.risk.greeks import marginal as marginal_fn
    m_delta, _ = marginal_fn(plan, 1)
    assert m_delta > 0  # this particular plan actually adds positive delta; assert sanity
    # Build a book instead where the held portfolio's sign is opposite the trade's marginal delta.
    portfolio = _portfolio(delta_dollars=14000.0)
    ctx = _ctx(portfolio=_portfolio(delta_dollars=-14000.0), chain_symbols=frozenset(leg.occ_symbol for leg in plan.legs))
    decision = evaluate(plan, ctx)
    # marginal delta is positive here and book is very negative -- cap must not be the binding one at qty=1.
    assert decision.approved


def test_vega_cap_resizes() -> None:
    legs = (_leg("SELL", 100.0, -0.28), _leg("BUY", 97.0, -0.10))
    plan = _plan(legs=legs, max_loss=Decimal("21"), max_profit=Decimal("9"), p_success=0.95)
    ctx = _ctx(portfolio=_portfolio(vega_dollars=1900.0), chain_symbols=frozenset(leg.occ_symbol for leg in legs))
    decision = evaluate(plan, ctx)
    from agent.risk.greeks import marginal as marginal_fn
    _, m_vega = marginal_fn(plan, 1)
    if decision.approved:
        assert abs(1900.0 + m_vega * decision.qty) <= 2000.0 + 1e-6
    else:
        assert decision.reason == GateReason.PORTFOLIO_VEGA_LIMIT


def test_llm_budget_ceiling_blocks_entry() -> None:
    plan = _plan()
    decision = evaluate(plan, _ctx(llm_budget_exhausted=True))
    assert not decision.approved
    assert decision.reason == GateReason.LLM_BUDGET_CEILING


def test_gate_context_default_keeps_day2_tests_green() -> None:
    ctx = _ctx()
    assert ctx.llm_budget_exhausted is False


def test_conviction_only_reduces_qty() -> None:
    # docs/day4_track_ab_plan.md §2.4: default plan's sized.qty is 8 (Kelly
    # capped by MAX_RISK_PER_TRADE_PCT), well under every other cap here, so
    # conviction is the only thing moving the final qty.
    plan = _plan()
    baseline = evaluate(plan, _ctx())
    assert baseline.approved and baseline.qty == 8

    full = evaluate(plan, _ctx(conviction=1.0))
    assert full.qty == baseline.qty

    halved = evaluate(plan, _ctx(conviction=0.5))
    assert halved.approved
    assert halved.qty == baseline.qty // 2

    zeroed = evaluate(plan, _ctx(conviction=0.0))
    assert not zeroed.approved
    assert zeroed.reason == GateReason.LOW_CONVICTION


def test_conviction_cannot_exceed_cap() -> None:
    """conviction is applied to q, never to any of the independent caps --
    a lower conviction can only ever tighten the binding cap's ceiling, never
    loosen it (docs/day4_track_ab_plan.md §2.4's invariant)."""
    plan = _plan()
    full = evaluate(plan, _ctx(buying_power=Decimal("300"), conviction=1.0))
    half = evaluate(plan, _ctx(buying_power=Decimal("300"), conviction=0.5))
    assert full.approved and full.qty == 1
    assert half.approved and half.qty == 1


def test_binding_constraint_reported() -> None:
    plan = _plan(max_loss=Decimal("5000"), max_profit=Decimal("2000"), width=100.0)
    decision = evaluate(plan, _ctx(buying_power=Decimal("100")))
    assert not decision.approved
    assert decision.reason in (GateReason.MAX_RISK_PER_TRADE, GateReason.INSUFFICIENT_BUYING_POWER)

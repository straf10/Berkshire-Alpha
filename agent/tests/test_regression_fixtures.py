"""Regression fixture for the eight live 2026-09-01 trades that produced the
$4,855.50 drawdown (docs/audit_report_v2.md). Each trade's stated post-fix
outcome is asserted directly against the remediated code, at the narrowest
unit that actually enforces it:

  1 DIA, 2 ORCL, 3 GS, 5 ORCL  -- blocked by Task 5 (SHORT_DELTA_OUT_OF_BAND
                                   on the LLM path, agent.agents.trader)
  4 NVDA                       -- still fills at 1.49 (walk-cap no-regression)
  6 LLY, 8 LLY                 -- walk cap 3.00 (width*0.6), UNFILLED_REJECT
  7 UBER                       -- unchanged: relative cap still binds, rejects

Trade 8 is the headline: it is the $4,380 loss, and this file proves it is
now provably impossible under the new walk cap.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from agent.execution.broker import MockBroker, OrderState
from agent.execution.order_manager import walk_to_fill
from agent.main import _max_loss_from_fill
from agent.schemas.execution import Intent, Leg, OrderStatus, Regime, RejectCode, SpreadPlan, Structure
from agent.schemas.market import ChainSnapshot, OptionQuote, QuantSnapshot
from agent.strategy.regime import RegimeDecision

EXPIRY = date(2026, 9, 4)


class FakeClock:
    def __init__(self) -> None:
        self._elapsed = 0.0

    def now(self) -> datetime:
        return datetime(2026, 9, 1, 15, tzinfo=timezone.utc) + timedelta(seconds=self._elapsed)

    async def sleep(self, seconds: float) -> None:
        self._elapsed += seconds


def _leg(side: str, strike: float, right: str, delta: float) -> Leg:
    intent = Intent.SELL_TO_OPEN if side == "SELL" else Intent.BUY_TO_OPEN
    return Leg(occ_symbol=f"TST260904{right}{int(strike*1000):08d}", strike=strike, right=right, side=side,
               ratio_qty=1, intent=intent, delta=delta, vega=0.05, bid=1.0, ask=1.1)


def _plan(
    structure: Structure, is_credit: bool, width: float, mid: str, natural: str, short_delta: float,
) -> SpreadPlan:
    right = "C" if structure in (Structure.BEAR_CALL_SPREAD, Structure.BULL_CALL_SPREAD) else "P"
    legs = (
        _leg("SELL", 100.0, right, -short_delta if right == "P" else short_delta),
        _leg("BUY", 100.0 - width if right == "P" else 100.0 + width, right, -0.10 if right == "P" else 0.10),
    )
    regime = Regime.CREDIT if is_credit else Regime.DEBIT
    return SpreadPlan(
        symbol="TST", structure=structure, regime=regime, expiry=EXPIRY, dte=3,
        legs=legs, width=width, net_mid=Decimal(mid), net_natural=Decimal(natural),
        max_profit_per_spread=Decimal("1"), max_loss_per_spread=Decimal("1"),
        p_success=0.5, spot=100.0, short_leg_delta=short_delta,
    )


def _state(order_id: str, status: OrderStatus, *, filled_qty: int = 0, fill_avg_price: Decimal | None = None) -> OrderState:
    return OrderState(order_id=order_id, status=status, limit_price=None, filled_qty=filled_qty,
                       total_qty=1, fill_avg_price=fill_avg_price, reject_code=None, reject_message=None)


# ---------------------------------------------------------------------------
# Trade 4 -- NVDA BULL_CALL_SPREAD, debit, width 2.50, mid 1.49, natural 1.51.
# No-regression case: walk cap is min(relative cap, width*0.6=1.50) = 1.50,
# still comfortably above the fill -- must still fill at 1.49 with zero walk.
# ---------------------------------------------------------------------------
async def test_trade4_nvda_still_fills_at_mid_no_regression() -> None:
    plan = _plan(Structure.BULL_CALL_SPREAD, is_credit=False, width=2.50, mid="1.49", natural="1.51", short_delta=0.4898)
    broker = MockBroker([
        _state("o1", OrderStatus.NEW),
        _state("o1", OrderStatus.FILLED, filled_qty=4, fill_avg_price=Decimal("1.49")),
    ])
    result = await walk_to_fill(broker, plan, 4, clock=FakeClock())
    assert result.status == "FILLED"
    assert result.fill_price == Decimal("1.49")
    assert result.steps == 0


# ---------------------------------------------------------------------------
# Trade 6 -- LLY BEAR_PUT_SPREAD, debit, width 5.00, mid 1.57, natural 6.57.
# Old cap: 1.57 + 0.70*(6.57-1.57) = 5.07 (101% of width). New cap: min(5.07,
# 5.00*0.60=3.00) = 3.00 -> UNFILLED_REJECT.
# ---------------------------------------------------------------------------
async def test_trade6_lly_walk_cap_clamped_to_width_unfilled_reject() -> None:
    plan = _plan(Structure.BEAR_PUT_SPREAD, is_credit=False, width=5.00, mid="1.57", natural="6.57", short_delta=0.0)
    broker = MockBroker([_state("o1", OrderStatus.NEW)])  # never fills -- repeats NEW
    result = await walk_to_fill(broker, plan, 2, clock=FakeClock())
    assert result.status == "UNFILLED_REJECT"
    assert result.final_limit is not None
    assert result.final_limit <= Decimal("3.00")


# ---------------------------------------------------------------------------
# Trade 8 -- LLY BEAR_PUT_SPREAD, debit, width 5.00, mid 1.94, natural 8.84.
# THE HEADLINE CASE. Old cap: 1.94 + 0.70*(8.84-1.94) = 6.77 (135% of width);
# the live walk filled at 6.65, a $1,884+ locked-in loss on a $5-wide vertical
# ($4,380 of the $4,855.50 total drawdown). New cap: min(6.77, 5.00*0.60=3.00)
# = 3.00 -- the walk must now cancel UNFILLED_REJECT and can never reach a
# fill above the spread's own maximum terminal value.
# ---------------------------------------------------------------------------
async def test_trade8_lly_headline_loss_now_provably_impossible() -> None:
    plan = _plan(Structure.BEAR_PUT_SPREAD, is_credit=False, width=5.00, mid="1.94", natural="8.84", short_delta=0.0)
    broker = MockBroker([_state("o1", OrderStatus.NEW)])  # never fills -- repeats NEW
    result = await walk_to_fill(broker, plan, 4, clock=FakeClock())
    assert result.status == "UNFILLED_REJECT"
    assert result.final_limit is not None
    # The walk must never reach anywhere near the live 6.65 fill, and never
    # exceed the spread's own maximum possible value (width = 5.00).
    assert result.final_limit <= Decimal("3.00")
    assert result.final_limit < Decimal("5.00")


# ---------------------------------------------------------------------------
# Trade 7 -- UBER BEAR_PUT_SPREAD, debit, width 1.00, mid 0.48, natural 0.56.
# Relative cap (0.48 + 0.70*0.08 = 0.536) is already tighter than the new
# width*0.6=0.60 absolute bound, so the clamp is a no-op here -- behaviour is
# unchanged: the walk still rejects around 0.53, same as live.
# ---------------------------------------------------------------------------
async def test_trade7_uber_walk_cap_unaffected_still_rejects() -> None:
    plan = _plan(Structure.BEAR_PUT_SPREAD, is_credit=False, width=1.00, mid="0.48", natural="0.56", short_delta=0.0)
    broker = MockBroker([_state("o1", OrderStatus.NEW)])  # never fills -- repeats NEW
    result = await walk_to_fill(broker, plan, 17, clock=FakeClock())
    assert result.status == "UNFILLED_REJECT"
    assert result.final_limit is not None
    assert result.final_limit <= Decimal("0.536")
    assert result.final_limit > Decimal("0.50")  # unaffected by the new width clamp


# ---------------------------------------------------------------------------
# Trades 1, 2, 3, 5 -- all four live LLM-built credit spreads breached
# SHORT_DELTA_BAND (0.22, 0.33), struck at 0.486-0.609 delta. Task 5 makes
# this impossible on the LLM path via validate_proposal.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "trade_id,symbol,structure,short_delta",
    [
        (1, "DIA", Structure.BEAR_CALL_SPREAD, 0.6089),
        (2, "ORCL", Structure.BULL_PUT_SPREAD, 0.5076),
        (3, "GS", Structure.BEAR_CALL_SPREAD, 0.5057),
        (5, "ORCL", Structure.BULL_PUT_SPREAD, 0.4859),
    ],
)
def test_trades_1_2_3_5_blocked_by_short_delta_band(trade_id, symbol, structure, short_delta) -> None:
    from agent.agents.trader import ProposalFailure, validate_proposal
    from agent.schemas.llm import OptionLegProposal, SpreadProposal
    from agent.schemas.market import ChainSnapshot, OptionQuote, QuantSnapshot
    from agent.strategy.regime import RegimeDecision

    right = "C" if structure == Structure.BEAR_CALL_SPREAD else "P"
    is_call = right == "C"
    sell_strike = 100.0
    buy_strike = 103.0 if is_call else 97.0

    contracts = (
        OptionQuote(occ_symbol=f"{symbol}{EXPIRY:%y%m%d}{right}{int(sell_strike*1000):08d}", underlying=symbol,
                    expiry=EXPIRY, strike=sell_strike, right=right, bid=1.0, ask=1.1,
                    delta=short_delta if is_call else -short_delta, gamma=0.01, theta=-0.01, vega=0.05, iv=0.3),
        OptionQuote(occ_symbol=f"{symbol}{EXPIRY:%y%m%d}{right}{int(buy_strike*1000):08d}", underlying=symbol,
                    expiry=EXPIRY, strike=buy_strike, right=right, bid=0.3, ask=0.4,
                    delta=0.10 if is_call else -0.10, gamma=0.01, theta=-0.01, vega=0.03, iv=0.3),
    )
    chain = ChainSnapshot(underlying=symbol, fetched_at=datetime(2026, 8, 28, tzinfo=timezone.utc), contracts=contracts)

    q = QuantSnapshot(
        symbol=symbol, session_date=date(2026, 8, 31), spot=100.0, rv_20=0.15, iv_atm=0.20,
        vrp_ratio=1.4, skew_abs=6.0, vwap=100.0, vwap_dev_pct=0.0, rsi=50.0, vwm=0.0,
        vwm_z=0.0, target_expiry=EXPIRY, dte=4, data_ok=True, drop_reason=None,
    )
    d = RegimeDecision(Regime.CREDIT, structure, "test", "TEST", None, None)

    proposal = SpreadProposal(
        underlying=symbol, strategy_name=structure.value, expiration_date="2026-09-04",
        legs=[
            OptionLegProposal(contract_type="CALL" if is_call else "PUT", side="SELL", strike_price=sell_strike, ratio_qty=1),
            OptionLegProposal(contract_type="CALL" if is_call else "PUT", side="BUY", strike_price=buy_strike, ratio_qty=1),
        ],
        confidence_score=0.8, reasoning="atm premium looked good",
    )
    trading_days = frozenset({date(2026, 8, 31), date(2026, 9, 2), date(2026, 9, 3), date(2026, 9, 4)})

    failure = validate_proposal(proposal, q, d, chain, trading_days)
    assert failure == ProposalFailure.SHORT_DELTA_OUT_OF_BAND, f"trade {trade_id} ({symbol}) must be blocked"


def test_compliant_delta_passes_validate_proposal() -> None:
    """0.275 delta (SHORT_DELTA_TARGET) must pass -- Task 5 rejects the
    breach, not the strategy itself."""
    from agent.agents.trader import validate_proposal
    from agent.schemas.llm import OptionLegProposal, SpreadProposal
    from agent.schemas.market import ChainSnapshot, OptionQuote, QuantSnapshot
    from agent.strategy.regime import RegimeDecision

    contracts = (
        OptionQuote(occ_symbol="TST260904P00100000", underlying="TST", expiry=EXPIRY, strike=100.0, right="P",
                    bid=1.0, ask=1.1, delta=-0.275, gamma=0.01, theta=-0.01, vega=0.05, iv=0.2),
        OptionQuote(occ_symbol="TST260904P00097000", underlying="TST", expiry=EXPIRY, strike=97.0, right="P",
                    bid=0.2, ask=0.3, delta=-0.10, gamma=0.01, theta=-0.01, vega=0.03, iv=0.2),
    )
    chain = ChainSnapshot(underlying="TST", fetched_at=datetime(2026, 8, 28, tzinfo=timezone.utc), contracts=contracts)
    q = QuantSnapshot(
        symbol="TST", session_date=date(2026, 8, 31), spot=100.0, rv_20=0.15, iv_atm=0.20,
        vrp_ratio=1.4, skew_abs=6.0, vwap=100.0, vwap_dev_pct=0.0, rsi=50.0, vwm=0.0,
        vwm_z=0.0, target_expiry=EXPIRY, dte=4, data_ok=True, drop_reason=None,
    )
    d = RegimeDecision(Regime.CREDIT, Structure.BULL_PUT_SPREAD, "test", "TEST", None, None)
    proposal = SpreadProposal(
        underlying="TST", strategy_name="bull put spread", expiration_date="2026-09-04",
        legs=[
            OptionLegProposal(contract_type="PUT", side="SELL", strike_price=100.0, ratio_qty=1),
            OptionLegProposal(contract_type="PUT", side="BUY", strike_price=97.0, ratio_qty=1),
        ],
        confidence_score=0.8, reasoning="band-compliant",
    )
    trading_days = frozenset({date(2026, 8, 31), date(2026, 9, 2), date(2026, 9, 3), date(2026, 9, 4)})
    assert validate_proposal(proposal, q, d, chain, trading_days) is None


def test_deterministic_fallback_still_band_compliant_when_retry_fails() -> None:
    """propose()'s deterministic fallback (spread_builder.build()) is
    band-compliant by construction -- assert it directly."""
    from agent.strategy.spread_builder import build

    contracts = (
        OptionQuote(occ_symbol="TST260904P00091000", underlying="TST", expiry=EXPIRY, strike=91.0, right="P",
                    bid=0.02, ask=0.04, delta=-0.05, gamma=0.01, theta=-0.01, vega=0.05, iv=0.2),
        OptionQuote(occ_symbol="TST260904P00097000", underlying="TST", expiry=EXPIRY, strike=97.0, right="P",
                    bid=0.10, ask=0.20, delta=-0.20, gamma=0.01, theta=-0.01, vega=0.05, iv=0.2),
        OptionQuote(occ_symbol="TST260904P00100000", underlying="TST", expiry=EXPIRY, strike=100.0, right="P",
                    bid=1.00, ask=1.10, delta=-0.275, gamma=0.01, theta=-0.01, vega=0.05, iv=0.2),
        OptionQuote(occ_symbol="TST260904P00103000", underlying="TST", expiry=EXPIRY, strike=103.0, right="P",
                    bid=2.50, ask=2.60, delta=-0.45, gamma=0.01, theta=-0.01, vega=0.05, iv=0.2),
    )
    chain = ChainSnapshot(underlying="TST", fetched_at=datetime(2026, 8, 28, tzinfo=timezone.utc), contracts=contracts)
    q = QuantSnapshot(
        symbol="TST", session_date=date(2026, 8, 31), spot=100.0, rv_20=0.15, iv_atm=0.20,
        vrp_ratio=1.4, skew_abs=6.0, vwap=100.0, vwap_dev_pct=0.0, rsi=50.0, vwm=0.0,
        vwm_z=0.0, target_expiry=EXPIRY, dte=4, data_ok=True, drop_reason=None,
    )
    d = RegimeDecision(Regime.CREDIT, Structure.BULL_PUT_SPREAD, "test", "TEST", None, None)
    plan = build(q, d, chain)
    assert isinstance(plan, SpreadPlan)
    assert 0.22 < plan.short_leg_delta < 0.33


# ---------------------------------------------------------------------------
# Task 3 -- post-fill risk recomputation reproduces the live DB exactly where
# the fill was at mid, and exposes the bug where it was not.
# ---------------------------------------------------------------------------
def test_task3_post_fill_risk_formula_matches_live_and_exposes_bug() -> None:
    dia_plan = _plan(Structure.BEAR_CALL_SPREAD, is_credit=True, width=1.00, mid="-0.55", natural="-0.44", short_delta=0.6089)
    assert _max_loss_from_fill(dia_plan, Decimal("-0.55")) == Decimal("45.0")  # DB 45.0, match

    nvda_plan = _plan(Structure.BULL_CALL_SPREAD, is_credit=False, width=2.50, mid="1.49", natural="1.51", short_delta=0.4898)
    assert _max_loss_from_fill(nvda_plan, Decimal("1.49")) == Decimal("149.0")  # DB 149.0, match

    lly_plan = _plan(Structure.BEAR_PUT_SPREAD, is_credit=False, width=5.00, mid="1.94", natural="8.84", short_delta=0.0)
    realized = _max_loss_from_fill(lly_plan, Decimal("6.65"))
    assert realized == Decimal("665.0")  # DB recorded 194.0 (stale, mid-based) -- bug exposed
    assert realized != Decimal("194.0")

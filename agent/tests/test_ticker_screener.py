from __future__ import annotations

import random
from dataclasses import replace
from datetime import date

from agent.config import UNIVERSE
from agent.schemas.execution import Regime, Structure
from agent.schemas.market import QuantSnapshot
from agent.strategy.regime import RegimeDecision
from agent.strategy.ticker_screener import assign_regimes, composite_score, shortlist

_BASE = QuantSnapshot(
    symbol="SPY",
    session_date=date(2026, 8, 31),
    spot=100.0,
    rv_20=0.15,
    iv_atm=0.20,
    vrp_ratio=1.4,
    skew_abs=6.0,           # skew overlay -> BULL_PUT_SPREAD, always directionally confirmed
    vwap=100.0,
    vwap_dev_pct=0.0,
    rsi=50.0,
    vwm=0.0,
    vwm_z=0.0,
    target_expiry=date(2026, 9, 4),
    dte=4,
    data_ok=True,
    drop_reason=None,
)


def _snap(symbol: str, **overrides) -> QuantSnapshot:
    return replace(_BASE, symbol=symbol, **overrides)


def test_shortlist_caps_at_four() -> None:
    snaps = [_snap(sym, skew_abs=6.0 + i) for i, sym in enumerate(UNIVERSE)]
    assigned = {s.symbol: Regime.CREDIT for s in snaps}
    result = shortlist(snaps, assigned)
    assert len(result) == 4


def test_shortlist_is_deterministic() -> None:
    snaps = [_snap("SPY", skew_abs=6.0), _snap("QQQ", skew_abs=6.0)]  # identical scores
    assigned = {"SPY": Regime.CREDIT, "QQQ": Regime.CREDIT}
    expected_order = ["SPY", "QQQ"]  # UNIVERSE index tiebreak: SPY(0) before QQQ(1)

    for _ in range(100):
        shuffled = snaps[:]
        random.shuffle(shuffled)
        result = shortlist(shuffled, assigned)
        assert [c.snapshot.symbol for c in result] == expected_order


def test_shortlist_excludes_no_trade() -> None:
    # docs/day4_track_ab_plan.md §1.6 removed the CREDIT_NO_DIRECTIONAL_CONFIRMATION
    # dead-end, so the only way a CREDIT-assigned, data_ok symbol reaches
    # NO_TRADE is by being absent from `assigned` entirely (defaults to
    # Regime.NO_TRADE) -- replaces the pre-Correction-3 version of this test.
    unassigned = _snap("SPY")
    tradeable = _snap("QQQ", skew_abs=6.0)
    assigned = {"QQQ": Regime.CREDIT}
    result = shortlist([unassigned, tradeable], assigned)
    symbols = [c.snapshot.symbol for c in result]
    assert "SPY" not in symbols
    assert "QQQ" in symbols


def test_assign_regimes_ranks_cross_sectionally() -> None:
    """The persisted 29-Aug cross-section (docs/day4_track_ab_plan.md §1.3
    sanity check) -> CREDIT {AAPL, TSLA, SPY}, DEBIT {NVDA, AMD, QQQ}."""
    vrps = {
        "AAPL": 1.258, "TSLA": 1.040, "SPY": 1.021,
        "MSFT": 1.00, "META": 0.98, "AMZN": 0.95, "GOOGL": 0.90,
        "NVDA": 0.769, "AMD": 0.804, "QQQ": 0.875,
    }
    snaps = [_snap(sym, vrp_ratio=v) for sym, v in vrps.items()]
    assigned = assign_regimes(snaps)
    credit = {s for s, r in assigned.items() if r == Regime.CREDIT}
    debit = {s for s, r in assigned.items() if r == Regime.DEBIT}
    assert credit == {"AAPL", "TSLA", "SPY"}
    assert debit == {"NVDA", "AMD", "QQQ"}


def test_assign_regimes_respects_sign_guards() -> None:
    # All 10 VRP >= 1.0 -- the bottom 3 by VRP must be NO_TRADE, not DEBIT.
    snaps = [_snap(sym, vrp_ratio=1.0 + 0.01 * i) for i, sym in enumerate(UNIVERSE)]
    assigned = assign_regimes(snaps)
    bottom_three = sorted(snaps, key=lambda q: q.vrp_ratio)[:3]
    for q in bottom_three:
        assert assigned[q.symbol] == Regime.NO_TRADE
    assert Regime.DEBIT not in assigned.values()


def test_assign_regimes_excludes_not_ok() -> None:
    snaps = [_snap(sym, vrp_ratio=1.0 + 0.05 * i) for i, sym in enumerate(UNIVERSE)]
    dropped = replace(snaps[0], data_ok=False, drop_reason="NO_CHAIN")
    snaps = [dropped, *snaps[1:]]

    assigned = assign_regimes(snaps)

    assert dropped.symbol not in assigned
    # 9 data_ok names >= 2*CROSS_SECTION_N(3) -> buckets stay at 3+3, not shrunk.
    assert len(assigned) == 6


def test_assign_regimes_shrinks_symmetrically_when_thin() -> None:
    # Only 4 data_ok names (< 2*CROSS_SECTION_N) -> both buckets shrink to 4//2 = 2.
    snaps = [_snap(sym, vrp_ratio=1.0 + 0.1 * i) for i, sym in enumerate(UNIVERSE[:4])]
    assigned = assign_regimes(snaps)
    assert len(assigned) == 4  # 2 credit-side + 2 debit-side, no middle excluded


def test_composite_score_uses_observed_range() -> None:
    """Two credit candidates both below the old 1.25 threshold must still rank
    distinctly -- both would score 0.0 under the retired absolute normaliser."""
    d = RegimeDecision(Regime.CREDIT, Structure.BULL_PUT_SPREAD, "x", "TEST", None, None)
    rich = _snap("AAPL", vrp_ratio=1.20, skew_abs=0.0, rsi=50.0)
    poor = _snap("TSLA", vrp_ratio=1.05, skew_abs=0.0, rsi=50.0)
    score_rich = composite_score(rich, d, vrp_lo=1.00, vrp_hi=1.20)
    score_poor = composite_score(poor, d, vrp_lo=1.00, vrp_hi=1.20)
    assert score_rich > score_poor

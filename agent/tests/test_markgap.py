from __future__ import annotations

from decimal import Decimal

from agent.tools.markgap import LegView, PositionView, SpreadInput, book_markgap, spread_mark

# The live book on 2026-09-03, read straight off `alpaca position list` at
# 14:52 UTC. A LONG (debit) LLY 1160/1165 put vertical, 4 contracts: the
# broker marked the SHORT 1160P at 13.90 and the LONG 1165P at 8.55 -- an
# ordering the strikes forbid, since a higher-strike put is never worth less
# than a lower-strike one. Net market value -2,140 on a structure whose value
# cannot go below zero.
LLY_LEGS = (
    LegView(occ_symbol="LLY260904P01160000", side="SELL", right="P", strike=1160.0),
    LegView(occ_symbol="LLY260904P01165000", side="BUY", right="P", strike=1165.0),
)
LLY_SPREAD = SpreadInput(
    trade_id=8, symbol="LLY", structure="BEAR_PUT_SPREAD", structure_is_credit=False,
    qty=4, legs=LLY_LEGS,
)
LLY_POSITIONS = {
    "LLY260904P01160000": PositionView(
        qty=Decimal("-4"), market_value=Decimal("-5560"), mark=Decimal("13.90")
    ),
    "LLY260904P01165000": PositionView(
        qty=Decimal("4"), market_value=Decimal("3420"), mark=Decimal("8.55")
    ),
}
LLY_SPOT = 1159.72


def test_live_lly_mark_is_below_the_arbitrage_floor() -> None:
    mark = spread_mark(LLY_SPREAD, LLY_POSITIONS, LLY_SPOT)
    assert mark is not None
    assert mark.width == Decimal("5.00")
    # 5.00 wide x 100 x 4 contracts. A long vertical is worth [0, 2000].
    assert (mark.band_low, mark.band_high) == (Decimal("0"), Decimal("2000.00"))
    assert mark.broker_mark == Decimal("-2140.00")
    # The whole finding: 2,140 dollars of loss the structure cannot produce.
    assert mark.markgap == Decimal("-2140.00")


def test_live_lly_intrinsic_sits_at_the_top_of_the_band() -> None:
    """Both puts ITM at 1159.72, so the vertical is worth its full width:
    (1165 - 1159.72) - (1160 - 1159.72) = 5.00 per spread."""
    mark = spread_mark(LLY_SPREAD, LLY_POSITIONS, LLY_SPOT)
    assert mark is not None
    assert mark.intrinsic == Decimal("2000.00")
    assert mark.band_low <= mark.intrinsic <= mark.band_high


def test_mark_inside_the_band_reports_exactly_zero() -> None:
    """No tolerance, no epsilon: a plausible mark is not a small markgap, it
    is no markgap. The panel's headline number must not drift on rounding."""
    positions = {
        "LLY260904P01160000": PositionView(
            qty=Decimal("-4"), market_value=Decimal("-3200"), mark=Decimal("8.00")
        ),
        "LLY260904P01165000": PositionView(
            qty=Decimal("4"), market_value=Decimal("4400"), mark=Decimal("11.00")
        ),
    }
    mark = spread_mark(LLY_SPREAD, positions, LLY_SPOT)
    assert mark is not None
    assert mark.broker_mark == Decimal("1200.00")
    assert mark.markgap == Decimal("0.00")


def test_credit_structure_band_is_the_mirror_image() -> None:
    """A short vertical is a liability: worth [-width, 0] to its writer. A
    mark below that floor is as impossible as one above zero."""
    spread = SpreadInput(
        trade_id=9, symbol="TST", structure="BULL_PUT_SPREAD", structure_is_credit=True,
        qty=2, legs=(
            LegView(occ_symbol="TST260904P00100000", side="SELL", right="P", strike=100.0),
            LegView(occ_symbol="TST260904P00097000", side="BUY", right="P", strike=97.0),
        ),
    )
    positions = {
        "TST260904P00100000": PositionView(
            qty=Decimal("-2"), market_value=Decimal("-1400"), mark=Decimal("7.00")
        ),
        "TST260904P00097000": PositionView(
            qty=Decimal("2"), market_value=Decimal("400"), mark=Decimal("2.00")
        ),
    }
    mark = spread_mark(spread, positions, spot=95.0)
    assert mark is not None
    assert (mark.band_low, mark.band_high) == (Decimal("-600.00"), Decimal("0"))
    assert mark.broker_mark == Decimal("-1000.00")
    assert mark.markgap == Decimal("-400.00")


def test_missing_leg_is_omitted_not_guessed() -> None:
    """One leg assigned, expired, or never filled: the spread has no
    meaningful band, and inventing one would be worse than showing nothing."""
    assert spread_mark(LLY_SPREAD, {"LLY260904P01160000": LLY_POSITIONS["LLY260904P01160000"]}, LLY_SPOT) is None


def test_leg_held_in_the_wrong_size_is_omitted() -> None:
    """Partial assignment: this trade's share of the symbol's market value
    cannot be attributed, so no bound can be drawn."""
    positions = dict(LLY_POSITIONS)
    positions["LLY260904P01160000"] = PositionView(
        qty=Decimal("-3"), market_value=Decimal("-4170"), mark=Decimal("13.90")
    )
    assert spread_mark(LLY_SPREAD, positions, LLY_SPOT) is None


def test_non_two_leg_and_zero_width_are_omitted() -> None:
    """The false-positive guard: width is DERIVED from the strikes, and a zero
    width would collapse the band to [0, 0] and report the entire mark as a
    markgap."""
    one_leg = SpreadInput(
        trade_id=10, symbol="LLY", structure="BEAR_PUT_SPREAD", structure_is_credit=False,
        qty=4, legs=(LLY_LEGS[0],),
    )
    assert spread_mark(one_leg, LLY_POSITIONS, LLY_SPOT) is None

    same_strike = SpreadInput(
        trade_id=11, symbol="LLY", structure="BEAR_PUT_SPREAD", structure_is_credit=False,
        qty=4, legs=(LLY_LEGS[0], LegView(occ_symbol="LLY260904P01165000", side="BUY", right="P", strike=1160.0)),
    )
    assert spread_mark(same_strike, LLY_POSITIONS, LLY_SPOT) is None


def test_unknown_spot_still_bounds_the_mark() -> None:
    """intrinsic needs a spot; the band and the markgap do not. The finding
    survives a stale or missing spots snapshot."""
    mark = spread_mark(LLY_SPREAD, LLY_POSITIONS, spot=None)
    assert mark is not None
    assert mark.intrinsic is None
    assert mark.markgap == Decimal("-2140.00")


def test_book_totals_and_omissions() -> None:
    unpriceable = SpreadInput(
        trade_id=12, symbol="GHOST", structure="BEAR_PUT_SPREAD", structure_is_credit=False,
        qty=1, legs=(
            LegView(occ_symbol="GHOST260904P00100000", side="SELL", right="P", strike=100.0),
            LegView(occ_symbol="GHOST260904P00105000", side="BUY", right="P", strike=105.0),
        ),
    )
    book = book_markgap(
        [LLY_SPREAD, unpriceable], LLY_POSITIONS, {"LLY": LLY_SPOT},
        computed_at="2026-09-03T14:52:00+00:00",
    )
    assert book["total_markgap"] == "-2140.00"
    assert book["omitted"] == 1
    assert len(book["spreads"]) == 1
    assert book["spreads"][0]["trade_id"] == 8
    assert book["computed_at"] == "2026-09-03T14:52:00+00:00"


def test_flat_book_reports_zero_not_an_error() -> None:
    """After the unwind the book is flat, and the panel still has to render."""
    book = book_markgap([], {}, {}, computed_at="2026-09-03T19:35:00+00:00")
    assert book["total_markgap"] == "0.00"
    assert book["spreads"] == []
    assert book["omitted"] == 0

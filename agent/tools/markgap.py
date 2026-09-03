"""Mark integrity for a book of vertical spreads.

The broker's mark is what the competition reads: `equity = cash + market
value`, and market value is whatever the broker says each leg is worth. On a
wide or stale options chain that number can leave the range the structure's
own strikes permit -- measured on this account 2026-09-03, a LONG LLY 1160/1165
put vertical was marked at -1,180 to -2,140, an impossibility for a structure
whose value is bounded below by zero (the broker was marking the short 1160P
above the long 1165P, an ordering the strikes forbid).

This module reports that distance and nothing more. Read the caveat on
`markgap` before quoting any number it produces: a markgap proves the mark is
impossible, NOT that the difference is collectible.

Pure -- no I/O, no clock, no broker, and deliberately no imports from
agent.execution. `OpenTrade` and `CliPosition` both live under
agent/execution/, so taking them here would drag order-placement code into
agent/tools/, whose whole purpose is to be importable by agent/storage/read.py
without doing that (see agent/tools/walk_cap.py's module docstring). Callers
adapt their own types into the small views below.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Mapping, Sequence

_CENT = Decimal("0.01")
_MULTIPLIER = Decimal("100")


def _quantize(x: Decimal) -> Decimal:
    return x.quantize(_CENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class LegView:
    """One leg of a spread, as the ORIGINAL entry placed it."""

    occ_symbol: str
    side: str        # "BUY" | "SELL" -- the entry side, not a closing side
    right: str       # "C" | "P"
    strike: float


@dataclass(frozen=True)
class PositionView:
    """One leg as the broker currently reports it."""

    qty: Decimal            # signed: negative == short
    market_value: Decimal   # signed dollars, the whole position at this symbol
    mark: Decimal           # per-contract price the broker is using


@dataclass(frozen=True)
class SpreadInput:
    trade_id: int
    symbol: str
    structure: str
    structure_is_credit: bool
    qty: int
    legs: tuple[LegView, ...]


@dataclass(frozen=True)
class SpreadMark:
    trade_id: int
    symbol: str
    structure: str
    qty: int
    width: Decimal
    broker_mark: Decimal          # what judged equity is carrying for this spread
    band_low: Decimal
    band_high: Decimal
    intrinsic: Decimal | None     # None when spot is unknown
    markgap: Decimal              # 0 inside the band; signed distance outside
    spot: float | None
    legs: tuple[dict[str, Any], ...]


def _leg_intrinsic(leg: LegView, spot: float) -> Decimal:
    """Per contract, unsigned. A put is worth max(K - S, 0), a call
    max(S - K, 0)."""
    strike = Decimal(str(leg.strike))
    s = Decimal(str(spot))
    return max(strike - s, Decimal("0")) if leg.right == "P" else max(s - strike, Decimal("0"))


def spread_mark(
    spread: SpreadInput, positions: Mapping[str, PositionView], spot: float | None
) -> SpreadMark | None:
    """None when the spread cannot be bounded honestly, which is every case
    where guessing would be worse than omitting the row:

    - not exactly two legs, or a zero strike width. `width` is DERIVED here
      (an open trade carries no width field), and a zero width would collapse
      the band to [0, 0] and report every cent of the mark as a markgap -- a
      spectacular false positive on the one panel whose entire claim is
      arithmetic rigour.
    - a leg the broker is not reporting (assigned, expired, never filled).
    - a leg held in a size that does not match the trade. Then this trade's
      share of that symbol's market value cannot be attributed, so no bound
      can be drawn. (MAX_POSITIONS_PER_UNDERLYING is 1, so two open trades
      cannot normally share a leg -- this catches partial assignment and
      partial fills, not routine overlap.)
    """
    if len(spread.legs) != 2 or spread.qty <= 0:
        return None

    width = _quantize(abs(Decimal(str(spread.legs[0].strike)) - Decimal(str(spread.legs[1].strike))))
    if width <= 0:
        return None

    broker_mark = Decimal("0")
    leg_rows: list[dict[str, Any]] = []
    for leg in spread.legs:
        pos = positions.get(leg.occ_symbol)
        if pos is None or abs(pos.qty) != Decimal(spread.qty):
            return None
        broker_mark += pos.market_value
        leg_rows.append({
            "occ_symbol": leg.occ_symbol,
            "side": leg.side,
            "right": leg.right,
            "strike": leg.strike,
            "qty": str(pos.qty),
            "mark": str(_quantize(pos.mark)),
            "market_value": str(_quantize(pos.market_value)),
        })

    # The arbitrage bounds, in dollars. A vertical's per-spread value lies in
    # [0, width] for the holder of a LONG (debit) structure and in
    # [-width, 0] for the writer of a SHORT (credit) one, where the negative
    # sign is the liability the writer carries.
    span = _quantize(width * _MULTIPLIER * Decimal(spread.qty))
    band_low, band_high = (-span, Decimal("0")) if spread.structure_is_credit else (Decimal("0"), span)

    broker_mark = _quantize(broker_mark)
    if broker_mark > band_high:
        markgap = broker_mark - band_high
    elif broker_mark < band_low:
        markgap = broker_mark - band_low
    else:
        markgap = Decimal("0")

    intrinsic: Decimal | None = None
    if spot is not None and spot > 0:
        per_spread = Decimal("0")
        for leg in spread.legs:
            sign = Decimal("1") if leg.side == "BUY" else Decimal("-1")
            per_spread += sign * _leg_intrinsic(leg, spot)
        intrinsic = _quantize(per_spread * _MULTIPLIER * Decimal(spread.qty))

    return SpreadMark(
        trade_id=spread.trade_id, symbol=spread.symbol, structure=spread.structure,
        qty=spread.qty, width=width, broker_mark=broker_mark,
        band_low=band_low, band_high=band_high, intrinsic=intrinsic,
        markgap=_quantize(markgap), spot=spot, legs=tuple(leg_rows),
    )


def book_markgap(
    spreads: Sequence[SpreadInput],
    positions: Mapping[str, PositionView],
    spots: Mapping[str, float],
    *,
    computed_at: str,
) -> dict[str, Any]:
    """The whole open book's mark integrity, shaped for `agent_state` and the
    dashboard. Every Decimal is stringified here rather than at the JSON
    boundary: put_state serialises with `default=str` anyway, so doing it
    explicitly keeps the stored shape identical whether a value happens to be
    Decimal or not.

    `omitted` counts spreads spread_mark() refused to bound, so a row that
    disappears from the panel is visibly skipped rather than silently dropped.
    """
    rows: list[dict[str, Any]] = []
    omitted = 0
    for spread in spreads:
        mark = spread_mark(spread, positions, spots.get(spread.symbol))
        if mark is None:
            omitted += 1
            continue
        rows.append({
            "trade_id": mark.trade_id,
            "symbol": mark.symbol,
            "structure": mark.structure,
            "qty": mark.qty,
            "width": str(mark.width),
            "broker_mark": str(mark.broker_mark),
            "band_low": str(mark.band_low),
            "band_high": str(mark.band_high),
            "intrinsic": None if mark.intrinsic is None else str(mark.intrinsic),
            "markgap": str(mark.markgap),
            "spot": mark.spot,
            "legs": list(mark.legs),
        })

    total = _quantize(sum((Decimal(r["markgap"]) for r in rows), Decimal("0")))
    return {
        "computed_at": computed_at,
        "spreads": rows,
        "omitted": omitted,
        "total_markgap": str(total),
        # Spot comes from the last entry scan's snapshot, so `intrinsic` can
        # lag the tape by up to a scan interval. broker_mark and the band --
        # which carry the finding -- need no spot at all.
        "intrinsic_spot_source": "last entry scan",
    }

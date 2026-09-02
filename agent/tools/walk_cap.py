from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from agent.config import (
    WALK_CAP_FRACTION,
    WALK_CAP_MAX_FRACTION_OF_WIDTH,
    WALK_CAP_MAX_FRACTION_OF_WIDTH_CLOSING,
)

# Split out of agent/execution/order_manager.py (docs/premarket_p1_p3_plan.md
# P3 first extracted it there, verbatim, out of the inline _walk logic).
# Moved one level further, to agent/tools/ -- a neutral, execution-agnostic
# location -- so agent/storage/read.py can compute the SAME cap for the M3
# walk-timeline chart (docs/review.md Task 4) without importing
# agent.execution, which test_api_import_graph deliberately bans from
# agent/api/app.py's dependency graph (the read-only API must never be able
# to reach the code that places orders). order_manager.py re-imports walk_cap
# from here rather than redefining it, so there is exactly one implementation
# -- the chart and the live walk can never disagree.

_CENT = Decimal("0.01")


def quantize_cent(x: Decimal) -> Decimal:
    return x.quantize(_CENT, rounding=ROUND_HALF_UP)


def walk_cap(*, mid: Decimal, natural: Decimal, width: float, is_closing: bool) -> Decimal:
    """Pure: no I/O, no clock.

    Bound the cap on the DIRECTION of the order actually being walked (mid's
    sign), not plan.structure -- plan.structure describes the ORIGINAL trade
    and is unchanged by build_closing_plan, so closing a credit spread is a
    debit order with plan.structure still CREDIT. Keying off plan.structure
    left that closing path completely unbounded (docs/review.md P0-2)."""
    cap = quantize_cent(mid + WALK_CAP_FRACTION * (natural - mid))
    is_debit_order = mid > 0
    if is_debit_order:
        # A vertical debit spread can never be worth more than its strike width, so a
        # debit above the width is an arbitrage-certain loss (audit_report_v2.md §4).
        # WALK_CAP_FRACTION alone is unbounded when the chain is wide -- clamp it.
        # A closing order (buying back a credit spread) legitimately costs
        # close to the full width once deep ITM, so it gets the wider bound.
        frac = WALK_CAP_MAX_FRACTION_OF_WIDTH_CLOSING if is_closing else WALK_CAP_MAX_FRACTION_OF_WIDTH
        cap = min(cap, quantize_cent(Decimal(str(width)) * frac))
    elif not is_closing:
        # Opening a credit structure: on a wide chain `natural` can itself be
        # positive, dragging `cap` across zero and letting the walk fill at a
        # net DEBIT for a structure that is supposed to collect a credit --
        # a guaranteed loss regardless of where the market moves
        # (docs/review.md P0-3). This floor rejects nothing a compliant,
        # in-band credit spread would ever reach -- it only forbids the sign
        # flip. Not applied on close: closing a debit spread is expected to
        # land in credit territory and should not be constrained here.
        cap = min(cap, Decimal("-0.01"))
    return cap

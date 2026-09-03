from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from agent.config import (
    WALK_CAP_CREDIT_SIGN_FLOOR,
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


def walk_cap(
    *, mid: Decimal, natural: Decimal, width: float, is_closing: bool,
    structure_is_credit: bool,
) -> Decimal:
    """Pure: no I/O, no clock.

    OPENING orders are bounded on the DIRECTION of the order being walked
    (mid's sign), because an opening plan's direction and its structure always
    agree. CLOSING orders are bounded on the ORIGINAL structure instead --
    `structure_is_credit`, i.e. STRUCTURE_IS_CREDIT[plan.structure], which
    build_closing_plan leaves untouched precisely because it describes the
    original trade (docs/review.md P0-2).

    Keying the closing branches off mid's sign is not merely stylistically
    worse, it is WRONG on a broken chain: an inverted quote can hand a long
    vertical a positive closing mid, and a sign-keyed rule would then take the
    debit branch and permit paying up to a full width to exit a structure
    whose value is bounded below by zero. That is not hypothetical -- see
    WALK_CAP_CREDIT_SIGN_FLOOR's config.py comment for the live 2026-09-03
    book that motivated this."""
    cap = quantize_cent(mid + WALK_CAP_FRACTION * (natural - mid))
    width_dec = Decimal(str(width))
    if is_closing:
        if structure_is_credit:
            # Buying back a SHORT vertical legitimately costs close to the
            # full width once deep ITM -- but never more than the width, which
            # is the most it can possibly be worth (audit_report_v2.md §4).
            cap = min(cap, quantize_cent(width_dec * WALK_CAP_MAX_FRACTION_OF_WIDTH_CLOSING))
        else:
            # Selling a LONG vertical. Its value is bounded below by zero, so
            # exiting at a net DEBIT is an arbitrage-certain giveaway: we would
            # be paying to hand away something that cannot be worth less than
            # nothing. Before this branch existed the closing path had no bound
            # here at all (docs/markgap_plan.md P0-A).
            cap = min(cap, WALK_CAP_CREDIT_SIGN_FLOOR)
    elif mid > 0:
        # Opening a debit: a vertical debit spread can never be worth more than
        # its strike width, so a debit above the width is an arbitrage-certain
        # loss (audit_report_v2.md §4). WALK_CAP_FRACTION alone is unbounded
        # when the chain is wide -- clamp it.
        cap = min(cap, quantize_cent(width_dec * WALK_CAP_MAX_FRACTION_OF_WIDTH))
    else:
        # Opening a credit structure: on a wide chain `natural` can itself be
        # positive, dragging `cap` across zero and letting the walk fill at a
        # net DEBIT for a structure that is supposed to collect a credit --
        # a guaranteed loss regardless of where the market moves
        # (docs/review.md P0-3). This floor rejects nothing a compliant,
        # in-band credit spread would ever reach -- it only forbids the sign
        # flip.
        cap = min(cap, WALK_CAP_CREDIT_SIGN_FLOOR)
    return cap

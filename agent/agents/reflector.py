from __future__ import annotations

import json
import statistics
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Final, Mapping, Sequence

from agent.agents.prompts import REFLECTOR_SYSTEM
from agent.config import FILL_RATE_FLOOR, MIN_FILL_SAMPLE
from agent.schemas.execution import STRUCTURE_IS_CREDIT, Structure
from agent.schemas.llm import ReflectorOutput
from agent.tools.llm import LlmPort, LlmUnavailable, LlmValidationDropped
from agent.tools.quant import SCREEN_STAGE_DATA_REJECTS
from agent.tools.walk_cap import quantize_cent, walk_cap

# agent/agents/* may never import agent.risk (test_agent_import_graph.py's
# test_agents_never_execute -- the gate takes no LLM input, so the LLM side
# must not reach INTO the gate's own enum either), so GateReason.APPROVED's
# value is inlined as a literal rather than imported. agent.risk.gates.
# GateReason.APPROVED == "APPROVED" is a StrEnum member; this repo already
# compares decisions.gate_reason against plain string literals everywhere
# else (see _row's callers, REFLECTOR_DENYLIST itself).
_APPROVED_GATE_REASON: Final[str] = "APPROVED"

# Day 4 (docs/day4_action_plan.md Step 5). Post-market critique agent. Same
# agent/agents/* contract as analysts.py/researchers.py/trader.py/risk_team.py:
# returns values, never persists, never imports agent.storage.write.

# P1 remediation (docs/audit_report_v2.md §7c/§9 item 9). Gates the Reflector
# may not propose loosening. These are the liquidity and execution
# guardrails; the 2026-09-01 reflection recommended loosening
# DEGENERATE_CHAIN on the same day an illiquid chain cost $4,380
# (audit_report_v2.md §7c) -- its reasoning was purely rejection COUNTS
# (88/200), with no reference to P&L. Rejection count is not evidence of
# over-tightness on the one gate that exists specifically to reject
# marginal-liquidity chains. MAX_QUOTE_SPREAD_PCT and the walk-cap constants
# are new P0 guardrails from the same audit -- denylisted pre-emptively so a
# future reflection cannot recommend loosening them either.
#
# docs/strategy_audit_and_loop.md S0 Task A2: agent.tools.quant.
# SCREEN_STAGE_DATA_REJECTS -- the same set read.py's _SCREEN_STAGE_REJECTS
# imports -- folded in here too. Those reasons (NO_CHAIN/DEGENERATE_CHAIN/
# NO_EXPIRY_IN_WINDOW/INSUFFICIENT_BARS/NO_ATM_IV/NO_SKEW_QUOTE/ZERO_RV/
# NO_MINUTE_BARS) are a data-availability floor, not a policy dial: "loosen
# NO_ATM_IV" is not a coherent proposal in the way "loosen DEGENERATE_CHAIN"
# at least parses as one. Before this fix the four newer members
# (NO_ATM_IV/NO_SKEW_QUOTE/ZERO_RV/NO_MINUTE_BARS) were absent, hand-copied
# out of sync with read.py's own (also-incomplete) copy of the same list.
REFLECTOR_DENYLIST: Final[frozenset[str]] = SCREEN_STAGE_DATA_REJECTS | {
    "MAX_QUOTE_SPREAD_PCT",
    # docs/fill_and_learning_plan.md P0-4/P1-1: WIDE_NET_SPREAD is the same
    # class of liquidity guardrail as the ones above -- it exists because a
    # chain this wide cannot be filled profitably, not because the agent is
    # too picky. FILL_RATE, below, is deliberately NOT in this set: it names
    # an execution-layer failure the Reflector should be free to report and
    # argue about (within a bounded proposed_change), unlike a liquidity gate
    # it must never argue to loosen.
    "WIDE_NET_SPREAD",
}


@dataclass(frozen=True)
class SessionDigest:
    """Deterministically computed from the session's decisions rows. Computing
    the binding constraint in Python rather than asking the model to find it
    keeps the model's job to argumentation, which is the part it is good at,
    and keeps the identified constraint auditable.

    `binding_constraint` is None when every gate_reason observed this session
    is in REFLECTOR_DENYLIST (P1 remediation, docs/audit_report_v2.md §7c/§9
    item 9) -- there is deliberately no fallback to the next-most-common
    reason in that case. `gate_histogram` still carries the full, unfiltered
    counts (including denylisted reasons) for context; only the binding-
    constraint SELECTION excludes them.

    M2 remediation (docs/review.md Task 7): the outcome block below applies
    the SAME rationale to P&L -- computing wins/losses/slippage in Python
    rather than asking the model to characterise its own outcome keeps the
    identified numbers auditable, and keeps the model's job argumentation
    over a fact it cannot shade. Every field defaults to the empty/zero case
    (no closed trades) so a session with nothing settled yet -- true for
    every session before Thursday's unwind -- digests exactly as it did
    before this block existed."""
    session_date: date
    decisions_examined: int
    binding_constraint: str | None
    constraint_count: int
    gate_histogram: tuple[tuple[str, int], ...]     # descending by count
    entered: int
    observed_range: tuple[float, float] | None      # min/max observed_value for the binding reason
    threshold: float | None
    closed_trades: int = 0
    realized_pnl: float = 0.0
    wins: int = 0
    avg_slippage_vs_mid: float = 0.0                # mean (fill_price - submitted_limit), signed, over filled trades
    worst_trade: tuple[str, float] | None = None    # (symbol, realized_pnl) of the worst closed trade

    # docs/fill_and_learning_plan.md P1-1: execution-layer facts, computed
    # from `trades` rows regardless of what `decisions.action` says --
    # `approved` (== the old `entered`) counts ENTER decisions, which is true
    # at approval time but says nothing about whether the trade ever reached
    # the market. On 2026-09-08 this was "10 entered, 1 FILLED" reported as
    # simply "10 entered".
    approved: int = 0
    submitted: int = 0
    filled: int = 0
    unfilled_reject: int = 0
    fill_rate: float = 1.0
    cap_bound_rejects: int = 0        # UNFILLED_REJECT rows where final_limit == the walk's own computed cap
    median_cap_headroom: float | None = None   # abs(cap - mid), over cap_bound_rejects
    median_net_width_pct: float | None = None  # over WIDE_NET_SPREAD gate rejections this session

    # docs/fill_and_learning_plan.md S5 Task 4: the payoff of P2's
    # counterfactual re-quotes -- whether EV_RETENTION's refusals were
    # actually calibrated. `forgone_pnl` and `avoided_loss` are reported
    # SEPARATELY and never netted: a net figure near zero is consistent both
    # with "the refusals were perfectly calibrated" and with "we missed $500
    # and dodged $500", and those demand opposite responses from the model.
    #
    # docs/strategy_audit_and_loop.md §5 B4: both are qty-WEIGHTED totals
    # (hypothetical_pnl * trades.qty, summed), not per-spread -- summing the
    # raw per-spread dollars across differently-sized positions understated
    # the 2026-09-09 cohort by more than 3x ($68/$440 per-spread vs
    # $211/$1,146 actual). would_have_filled_n (§0 D1) is dropped: the column
    # it counted is hardcoded True at write time and carries zero information.
    counterfactual_n: int = 0            # unfilled entries with >=1 counterfactual sample
    forgone_pnl: float = 0.0             # qty-weighted $ summed over sampled entries
    avoided_loss: float = 0.0            # same sum, restricted to negative values only


def _recompute_cap(t: Mapping[str, Any]) -> tuple[Decimal, Decimal] | None:
    """The cap the live walk actually enforced -- (cap, mid), or None if
    plan_json is absent/unparseable. Pure: no I/O.

    docs/fill_and_learning_plan.md S5 Task 1/2: prefers the persisted
    `trades.final_cap`, which is the walk's OWN final cap after any P0-3
    mid-walk re-quote. Recomputing from plan_json alone (the old behaviour)
    silently disagreed with the live walk the moment a re-quote moved the
    cap -- on 2026-09-09 that made this diagnostic report "1 of 4 unfilled
    rejections stopped at the cap" when the true answer was 4 of 4. Falls
    back to recomputing from plan_json only for rows written before
    `final_cap` existed (final_cap is NULL)."""
    plan_json = t.get("plan_json")
    if not plan_json:
        return None
    try:
        p = json.loads(plan_json)
        mid = quantize_cent(Decimal(str(p["net_mid"])))
        final_cap = t.get("final_cap")
        if final_cap is not None:
            return quantize_cent(Decimal(str(final_cap))), mid
        natural = quantize_cent(Decimal(str(p["net_natural"])))
        structure = Structure(p["structure"])
        ps = Decimal(str(p["p_success"]))
        max_profit = Decimal(str(p["max_profit_per_spread"]))
        max_loss = Decimal(str(p["max_loss_per_spread"]))
        ev_at_mid = ps * max_profit - (Decimal("1") - ps) * max_loss
        cap = walk_cap(
            mid=mid, natural=natural, width=float(p["width"]), is_closing=False,
            structure_is_credit=STRUCTURE_IS_CREDIT[structure], ev_at_mid=ev_at_mid,
        )
        return cap, mid
    except Exception:  # noqa: BLE001 -- a malformed row costs itself, not the digest
        return None


def digest(rows: Sequence[Mapping[str, Any]], trades: Sequence[Mapping[str, Any]] = ()) -> SessionDigest:
    """Pure. `rows` are decisions rows for one session_date; `trades` are the
    trades rows entered during it (joined on decision_id, since trades carries
    no session_date of its own) -- both passed in by main.py, this module
    performs no queries. Ties in the gate_reason histogram break toward the
    reason that appeared first in `rows` (already ts_utc-ordered by main.py's
    query), so the winner is deterministic rather than dependent on dict
    iteration order.

    `trades` defaults to `()`: every session before Thursday's unwind has zero
    closed rows (realized_pnl/closed_at are unpopulated until then), and every
    existing call site that only ever passed decisions rows keeps working
    unchanged (docs/review.md Task 7)."""
    counts: dict[str, int] = {}
    first_seen: dict[str, int] = {}
    for i, row in enumerate(rows):
        reason = row["gate_reason"]
        counts[reason] = counts.get(reason, 0) + 1
        first_seen.setdefault(reason, i)

    gate_histogram = tuple(
        sorted(counts.items(), key=lambda kv: (-kv[1], first_seen[kv[0]]))
    )

    # docs/fill_and_learning_plan.md P1-1: execution facts, computed from
    # `trades` regardless of `decisions.action` -- a decision can be
    # ENTER/APPROVED and still never reach the market.
    approved = sum(1 for row in rows if row["action"] == "ENTER")
    submitted = len(trades)
    filled_trades = [t for t in trades if t.get("status") == "FILLED"]
    filled_count = len(filled_trades)
    unfilled_rejects = [t for t in trades if t.get("status") == "UNFILLED_REJECT"]
    fill_rate = (filled_count / submitted) if submitted else 1.0

    cap_headrooms: list[float] = []
    cap_bound_rejects = 0
    for t in unfilled_rejects:
        recomputed = _recompute_cap(t)
        if recomputed is None:
            continue
        cap, mid = recomputed
        final_limit = t.get("final_limit")
        if final_limit is not None and quantize_cent(Decimal(str(final_limit))) == cap:
            cap_bound_rejects += 1
        cap_headrooms.append(float(abs(cap - mid)))
    median_cap_headroom = statistics.median(cap_headrooms) if cap_headrooms else None

    # docs/fill_and_learning_plan.md S5 Task 4: `t["counterfactual"]` is the
    # latest P2 re-quote sample main._session_trades attaches to each
    # UNFILLED_REJECT row, or None if none was ever recorded (e.g. the
    # contract expired before the next management tick).
    #
    # docs/strategy_audit_and_loop.md §5 B4: qty-weighted -- hypothetical_pnl
    # is dollars PER SPREAD, so a 1-lot and a 6-lot refusal must not count
    # equally. `t.get("qty", 1)` covers rows/fixtures predating this field.
    cf_sampled = [t for t in unfilled_rejects if t.get("counterfactual") is not None]
    counterfactual_n = len(cf_sampled)
    cf_qty_pnls = [t["counterfactual"]["hypothetical_pnl"] * t.get("qty", 1) for t in cf_sampled]
    forgone_pnl = sum(cf_qty_pnls)
    avoided_loss = sum(p for p in cf_qty_pnls if p < 0)

    wide_spread_rows = [
        row for row in rows if row["gate_reason"] == "WIDE_NET_SPREAD" and row["observed_value"] is not None
    ]
    median_net_width_pct = (
        statistics.median(row["observed_value"] for row in wide_spread_rows) if wide_spread_rows else None
    )

    # P1 remediation: FILL_RATE is checked BEFORE the gate_reason histogram
    # -- a session where the agent could not get filled has exactly one
    # binding constraint, and it is not whatever gate_reason happens to be
    # most common upstream of execution (docs/fill_and_learning_plan.md
    # P1-1: on 2026-09-08 the Reflector argued to tighten NO_REGIME on a day
    # execution, not selection, was the entire problem).
    if submitted >= MIN_FILL_SAMPLE and fill_rate < FILL_RATE_FLOOR:
        binding_constraint = "FILL_RATE"
        constraint_count = len(unfilled_rejects)
        observed_range = (fill_rate, fill_rate)
        threshold = FILL_RATE_FLOOR
    else:
        # P1 remediation: the binding constraint the Reflector argues about must
        # never be a liquidity/execution guardrail (REFLECTOR_DENYLIST) -- reject
        # candidacy, don't just downrank it, or a session dominated by
        # DEGENERATE_CHAIN rejections would still hand the model the next-most-
        # common reason to build a "loosen this" argument around.
        #
        # docs/strategy_audit_and_loop.md S0 Task A3: `counts` is built from
        # EVERY decisions row's gate_reason, including the entered ones --
        # those carry gate_reason=GateReason.APPROVED (agent/risk/gates.py),
        # a success outcome, not a rejection. Undenylisted, a session with a
        # good fill rate (so the FILL_RATE branch above never fires) and a
        # plurality of ENTER decisions would hand the model "APPROVED" as the
        # thing standing between it and more trades -- nonsensical, since
        # nothing rejected anything. REFLECTOR_DENYLIST stays reserved for
        # actual guardrails the Reflector must never argue to loosen; this is
        # a separate exclusion because APPROVED isn't a guardrail at all.
        candidates = {
            r: c for r, c in counts.items()
            if r not in REFLECTOR_DENYLIST and r != _APPROVED_GATE_REASON
        }
        if not candidates:
            binding_constraint = None
            constraint_count = 0
            observed_range = None
            threshold = None
        else:
            binding_constraint = min(candidates, key=lambda reason: (-candidates[reason], first_seen[reason]))
            constraint_count = candidates[binding_constraint]
            binding_rows = [row for row in rows if row["gate_reason"] == binding_constraint]
            observed = [row["observed_value"] for row in binding_rows if row["observed_value"] is not None]
            observed_range = (min(observed), max(observed)) if observed else None
            threshold = next(
                (row["threshold_value"] for row in binding_rows if row["threshold_value"] is not None), None
            )

    # M2 remediation (docs/review.md Task 7): realized_pnl/closed_at are only
    # meaningful once a trade has actually closed -- wins/realized_pnl/
    # worst_trade are scoped to `closed`. Slippage, by contrast, is an entry-
    # time fact that exists the moment a trade fills, closed or not, so it is
    # scoped to every trade with a fill_price instead.
    closed = [t for t in trades if t["closed_at"] is not None]
    closed_trades = len(closed)
    realized_pnl = sum((t["realized_pnl"] or 0.0) for t in closed)
    wins = sum(1 for t in closed if (t["realized_pnl"] or 0.0) > 0)
    worst_trade = (
        min(closed, key=lambda t: t["realized_pnl"] or 0.0)
        if closed else None
    )
    worst_trade_pair = (worst_trade["symbol"], worst_trade["realized_pnl"]) if worst_trade is not None else None

    slippage_rows = [t for t in trades if t["fill_price"] is not None]
    avg_slippage_vs_mid = (
        sum(t["fill_price"] - t["submitted_limit"] for t in slippage_rows) / len(slippage_rows)
        if slippage_rows else 0.0
    )

    return SessionDigest(
        session_date=date.fromisoformat(rows[0]["session_date"]),
        decisions_examined=len(rows),
        binding_constraint=binding_constraint,
        constraint_count=constraint_count,
        gate_histogram=gate_histogram,
        entered=sum(1 for row in rows if row["action"] == "ENTER"),
        observed_range=observed_range,
        threshold=threshold,
        closed_trades=closed_trades,
        realized_pnl=realized_pnl,
        wins=wins,
        avg_slippage_vs_mid=avg_slippage_vs_mid,
        worst_trade=worst_trade_pair,
        approved=approved,
        submitted=submitted,
        filled=filled_count,
        unfilled_reject=len(unfilled_rejects),
        fill_rate=fill_rate,
        cap_bound_rejects=cap_bound_rejects,
        median_cap_headroom=median_cap_headroom,
        median_net_width_pct=median_net_width_pct,
        counterfactual_n=counterfactual_n,
        forgone_pnl=forgone_pnl,
        avoided_loss=avoided_loss,
    )


def _prompt(d: SessionDigest) -> str:
    histogram = ", ".join(f"{reason}={count}" for reason, count in d.gate_histogram)
    if d.binding_constraint is not None:
        if d.observed_range is not None and d.threshold is not None:
            observed_line = (
                f"Observed values against that gate's threshold ranged "
                f"{d.observed_range[0]:.3f} to {d.observed_range[1]:.3f}, threshold {d.threshold:.3f}."
            )
        else:
            observed_line = "No observed/threshold values were recorded against that gate."
        constraint_block = (
            f"Binding constraint: {d.binding_constraint}, accounting for {d.constraint_count} of "
            f"{d.decisions_examined} decisions.\n"
            f"Full gate-reason histogram: {histogram}.\n"
            f"{observed_line}"
        )
    else:
        # M2 remediation (docs/review.md Task 7): reaching reflect() at all
        # with binding_constraint=None means every observed gate reason was
        # denylisted BUT at least one trade closed this session -- say so
        # explicitly rather than printing "Binding constraint: None", which
        # would otherwise read as a data gap rather than a deliberate
        # exclusion the model must not try to route around.
        constraint_block = (
            "Binding constraint: none eligible -- every gate rejection observed this session was "
            "against a denylisted liquidity/execution guardrail (see REFLECTOR_DENYLIST); this agent "
            "will not argue to loosen those regardless of rejection volume.\n"
            f"Full gate-reason histogram: {histogram}."
        )

    outcome_block = ""
    if d.closed_trades > 0:
        worst = f"{d.worst_trade[0]} {d.worst_trade[1]:+.2f}" if d.worst_trade is not None else "n/a"
        outcome_block = (
            f"\nRealized outcome: {d.closed_trades} closed trade(s), {d.wins} win(s), "
            f"total realized P&L {d.realized_pnl:+.2f}, worst trade {worst}, "
            f"average fill slippage vs mid {d.avg_slippage_vs_mid:+.3f} per share."
        )

    # docs/fill_and_learning_plan.md P1-1: execution facts, always shown when
    # anything was submitted -- "N approved" is not the same claim as "N
    # reached the market", and conflating them is exactly what produced the
    # 2026-09-08 TIGHTEN-NO_REGIME verdict on a day execution was the entire
    # problem.
    execution_block = ""
    if d.submitted > 0:
        cap_detail = ""
        if d.cap_bound_rejects > 0:
            headroom = f"{d.median_cap_headroom:.3f}" if d.median_cap_headroom is not None else "n/a"
            cap_detail = (
                f" Of the unfilled rejections, {d.cap_bound_rejects} stopped exactly at the "
                f"walk's own computed cap (median unspent headroom {headroom})."
            )
        width_detail = (
            f" Median net spread width on WIDE_NET_SPREAD rejections: {d.median_net_width_pct:.1%}."
            if d.median_net_width_pct is not None else ""
        )
        cf_detail = ""
        if d.counterfactual_n > 0:
            cf_detail = (
                f"\nCounterfactuals (P2 re-quotes of unfilled entries): {d.counterfactual_n} sampled. "
                f"Forgone P&L (gains missed by refusing) {d.forgone_pnl:+.2f}; avoided loss (losses "
                f"dodged by refusing) {d.avoided_loss:+.2f} -- both qty-weighted portfolio dollars, not "
                f"per-spread. These are reported separately and must NOT be netted -- a sum near zero "
                f"can mean the refusals were well-calibrated OR that equal gains and losses were both "
                f"missed, and those call for opposite responses."
            )
        execution_block = (
            f"\nExecution: {d.submitted} submitted, {d.filled} FILLED, {d.unfilled_reject} unfilled-rejected "
            f"({d.fill_rate:.1%} fill rate).{cap_detail}{width_detail}{cf_detail}"
        )

    return (
        f"Session {d.session_date.isoformat()}: {d.decisions_examined} candidates evaluated, "
        f"{d.approved} approved, {d.submitted} submitted, {d.filled} FILLED ({d.fill_rate:.1%} fill rate).\n"
        f"{constraint_block}"
        f"{execution_block}"
        f"{outcome_block}"
    )


@dataclass(frozen=True)
class ReflectionResult:
    digest: SessionDigest
    output: ReflectorOutput | None      # None iff the call failed
    ok: bool


async def reflect(llm: LlmPort, d: SessionDigest, *, sink: list[int]) -> ReflectionResult:
    """ONE call, node='REFLECTOR'. Never raises: an LlmUnavailable (transport,
    budget) or LlmValidationDropped (bad schema twice) is caught and returned
    as ok=False so the deterministic digest is still persisted -- a failed
    reflection must not lose the session's constraint histogram.

    P1 remediation (docs/audit_report_v2.md §9 item 9): when digest() found no
    non-denylisted binding constraint, this returns a null/no-verdict result
    with ZERO LLM calls, rather than falling through to argue about the
    next-most-common (denylisted) gate. The deterministic digest -- including
    the full gate_histogram -- is still persisted by the caller.

    M2 remediation (docs/review.md Task 7): that skip is narrowed to sessions
    with zero closed trades. A denylisted-only gate histogram with real,
    closed P&L attached is not "nothing to reflect on" -- it is exactly the
    2026-09-01 scenario this module exists to prevent (a LOOSEN verdict on
    DEGENERATE_CHAIN, argued from rejection counts alone, the same day an
    illiquid chain cost $4,380). REFLECTOR_DENYLIST still applies: the model
    is told which gates it may not argue to loosen (see _prompt), it is just
    no longer excused from looking at the money."""
    if d.binding_constraint is None and d.closed_trades == 0:
        return ReflectionResult(digest=d, output=None, ok=False)
    try:
        output = await llm.complete_json(
            _prompt(d), ReflectorOutput, node="REFLECTOR", system=REFLECTOR_SYSTEM, sink=sink
        )
    except (LlmUnavailable, LlmValidationDropped):
        return ReflectionResult(digest=d, output=None, ok=False)
    return ReflectionResult(digest=d, output=output, ok=True)

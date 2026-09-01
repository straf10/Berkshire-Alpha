from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Final, Mapping, Sequence

from agent.agents.prompts import REFLECTOR_SYSTEM
from agent.schemas.llm import ReflectorOutput
from agent.tools.llm import LlmPort, LlmUnavailable, LlmValidationDropped

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
REFLECTOR_DENYLIST: Final[frozenset[str]] = frozenset({
    "DEGENERATE_CHAIN", "MAX_QUOTE_SPREAD_PCT", "NO_CHAIN",
})


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
    constraint SELECTION excludes them."""
    session_date: date
    decisions_examined: int
    binding_constraint: str | None
    constraint_count: int
    gate_histogram: tuple[tuple[str, int], ...]     # descending by count
    entered: int
    observed_range: tuple[float, float] | None      # min/max observed_value for the binding reason
    threshold: float | None


def digest(rows: Sequence[Mapping[str, Any]]) -> SessionDigest:
    """Pure. `rows` are decisions rows for one session_date, passed in by
    main.py -- this module performs no queries. Ties in the gate_reason
    histogram break toward the reason that appeared first in `rows` (already
    ts_utc-ordered by main.py's query), so the winner is deterministic rather
    than dependent on dict iteration order."""
    counts: dict[str, int] = {}
    first_seen: dict[str, int] = {}
    for i, row in enumerate(rows):
        reason = row["gate_reason"]
        counts[reason] = counts.get(reason, 0) + 1
        first_seen.setdefault(reason, i)

    gate_histogram = tuple(
        sorted(counts.items(), key=lambda kv: (-kv[1], first_seen[kv[0]]))
    )

    # P1 remediation: the binding constraint the Reflector argues about must
    # never be a liquidity/execution guardrail (REFLECTOR_DENYLIST) -- reject
    # candidacy, don't just downrank it, or a session dominated by
    # DEGENERATE_CHAIN rejections would still hand the model the next-most-
    # common reason to build a "loosen this" argument around.
    candidates = {r: c for r, c in counts.items() if r not in REFLECTOR_DENYLIST}
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

    return SessionDigest(
        session_date=date.fromisoformat(rows[0]["session_date"]),
        decisions_examined=len(rows),
        binding_constraint=binding_constraint,
        constraint_count=constraint_count,
        gate_histogram=gate_histogram,
        entered=sum(1 for row in rows if row["action"] == "ENTER"),
        observed_range=observed_range,
        threshold=threshold,
    )


def _prompt(d: SessionDigest) -> str:
    histogram = ", ".join(f"{reason}={count}" for reason, count in d.gate_histogram)
    if d.observed_range is not None and d.threshold is not None:
        observed_line = (
            f"Observed values against that gate's threshold ranged "
            f"{d.observed_range[0]:.3f} to {d.observed_range[1]:.3f}, threshold {d.threshold:.3f}."
        )
    else:
        observed_line = "No observed/threshold values were recorded against that gate."
    return (
        f"Session {d.session_date.isoformat()}: {d.decisions_examined} candidates evaluated, "
        f"{d.entered} entered.\n"
        f"Binding constraint: {d.binding_constraint}, accounting for {d.constraint_count} of "
        f"{d.decisions_examined} decisions.\n"
        f"Full gate-reason histogram: {histogram}.\n"
        f"{observed_line}"
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
    the full gate_histogram -- is still persisted by the caller."""
    if d.binding_constraint is None:
        return ReflectionResult(digest=d, output=None, ok=False)
    try:
        output = await llm.complete_json(
            _prompt(d), ReflectorOutput, node="REFLECTOR", system=REFLECTOR_SYSTEM, sink=sink
        )
    except (LlmUnavailable, LlmValidationDropped):
        return ReflectionResult(digest=d, output=None, ok=False)
    return ReflectionResult(digest=d, output=output, ok=True)

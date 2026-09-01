from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Mapping, Sequence

from agent.agents.prompts import REFLECTOR_SYSTEM
from agent.schemas.llm import ReflectorOutput
from agent.tools.llm import LlmPort, LlmUnavailable, LlmValidationDropped

# Day 4 (docs/day4_action_plan.md Step 5). Post-market critique agent. Same
# agent/agents/* contract as analysts.py/researchers.py/trader.py/risk_team.py:
# returns values, never persists, never imports agent.storage.write.


@dataclass(frozen=True)
class SessionDigest:
    """Deterministically computed from the session's decisions rows. Computing
    the binding constraint in Python rather than asking the model to find it
    keeps the model's job to argumentation, which is the part it is good at,
    and keeps the identified constraint auditable."""
    session_date: date
    decisions_examined: int
    binding_constraint: str
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

    binding_constraint = min(counts, key=lambda reason: (-counts[reason], first_seen[reason]))
    gate_histogram = tuple(
        sorted(counts.items(), key=lambda kv: (-kv[1], first_seen[kv[0]]))
    )

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
        constraint_count=counts[binding_constraint],
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
    reflection must not lose the session's constraint histogram."""
    try:
        output = await llm.complete_json(
            _prompt(d), ReflectorOutput, node="REFLECTOR", system=REFLECTOR_SYSTEM, sink=sink
        )
    except (LlmUnavailable, LlmValidationDropped):
        return ReflectionResult(digest=d, output=None, ok=False)
    return ReflectionResult(digest=d, output=output, ok=True)

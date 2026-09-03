import { formatDayMonth, formatTimeUtc, modeLabel } from "@/lib/format";
import type { Decision } from "@/lib/types";

// Client-side faceting over the decisions window the page already fetched
// (app/page.tsx's `?limit=200`). Nothing here queries the API: the feed's
// filters, counts, sort and histogram all read the same array, which is why
// the UI is allowed to say "filtered client-side" next to them.

// Five facets, in the order they read: what the agent did, why it stopped,
// how it decided, what the market looked like, and which scan slot it was.
export const FACET_IDS = ["action", "outcome", "mode", "regime", "scan"] as const;
export type FacetId = (typeof FACET_IDS)[number];

export const FACET_LABEL: Record<FacetId, string> = {
  action: "Action",
  outcome: "Outcome",
  mode: "Mode",
  regime: "Regime",
  scan: "Scan",
};

// The scan facet keys on cycle_id, not on a formatted time: cycle_id is what
// the row actually carries (agent/main.py:816 writes one uuid per scan), and
// a 200-row window can span more than one session, where two scans share a
// wall-clock slot. The human label is derived from the cycle's first row.
const VALUE_OF: Record<FacetId, (d: Decision) => string> = {
  action: (d) => d.action,
  outcome: (d) => d.gate_reason,
  mode: (d) => d.mode,
  regime: (d) => d.regime,
  scan: (d) => d.cycle_id,
};

// Every label map is a lookup with a passthrough default, never an exhaustive
// switch: the gate publishes 20+ GateReason members plus the screen- and
// deliberation-stage codes, and historical rows carry retired ones
// (DEBATE_UNRESOLVED) that no enum in the codebase lists any more.
function labelFor(facet: FacetId, value: string, scanLabels: Map<string, string>): string {
  if (facet === "mode") return modeLabel(value);
  if (facet === "scan") return scanLabels.get(value) ?? value.slice(0, 8);
  return value;
}

export interface FacetValue {
  value: string;
  label: string;
  count: number;
}

export interface Facet {
  id: FacetId;
  label: string;
  values: FacetValue[];
}

export type FacetSelection = Readonly<Record<FacetId, ReadonlySet<string>>>;

export function emptySelection(): FacetSelection {
  return {
    action: new Set(),
    outcome: new Set(),
    mode: new Set(),
    regime: new Set(),
    scan: new Set(),
  };
}

export function selectionSize(selection: FacetSelection): number {
  return FACET_IDS.reduce((n, id) => n + selection[id].size, 0);
}

export function toggleFacetValue(
  selection: FacetSelection,
  facet: FacetId,
  value: string
): FacetSelection {
  const next = new Set(selection[facet]);
  if (!next.delete(value)) next.add(value);
  return { ...selection, [facet]: next };
}

export function withFacetValues(
  selection: FacetSelection,
  facet: FacetId,
  values: readonly string[]
): FacetSelection {
  return { ...selection, [facet]: new Set(values) };
}

// One label per cycle: "14:15", or "2 Sep 14:15" when the window covers more
// than one session and a bare time would be ambiguous.
function scanLabels(decisions: readonly Decision[]): Map<string, string> {
  const first = new Map<string, Decision>();
  for (const d of decisions) {
    const seen = first.get(d.cycle_id);
    if (!seen || d.ts_utc < seen.ts_utc) first.set(d.cycle_id, d);
  }
  const multiSession = new Set(decisions.map((d) => d.session_date)).size > 1;
  const labels = new Map<string, string>();
  for (const [cycleId, d] of first) {
    const time = formatTimeUtc(d.ts_utc);
    labels.set(cycleId, multiSession ? `${formatDayMonth(d.ts_utc)} ${time}` : time);
  }
  return labels;
}

// Counts are over the UNFILTERED window, deliberately: a chip whose count
// reacted to the other chips would drop to zero and vanish the moment you
// used it, so the panel would rearrange itself under the cursor. The count
// answers "how many rows would this chip give me", not "how many are showing".
//
// `selection` is read only so that a value which is selected but absent from
// the window -- a ?gate= deep link naming a reason no row in the last 200
// carries -- still renders a chip, at count 0, that can be clicked off again.
export function buildFacets(decisions: readonly Decision[], selection: FacetSelection): Facet[] {
  const labels = scanLabels(decisions);
  const cycleStart = new Map<string, string>();
  for (const d of decisions) {
    const seen = cycleStart.get(d.cycle_id);
    if (!seen || d.ts_utc < seen) cycleStart.set(d.cycle_id, d.ts_utc);
  }

  return FACET_IDS.map((id) => {
    const counts = new Map<string, number>();
    for (const value of selection[id]) counts.set(value, 0);
    for (const d of decisions) {
      const value = VALUE_OF[id](d);
      counts.set(value, (counts.get(value) ?? 0) + 1);
    }

    const values = [...counts].map(([value, count]) => ({
      value,
      label: labelFor(id, value, labels),
      count,
    }));

    // Scans are a timeline -- chronological, so the chips read left to right
    // as the session did. Every other facet ranks by how much of the session
    // it accounts for, which puts the reason that actually bound the agent
    // first.
    values.sort((a, b) =>
      id === "scan"
        ? (cycleStart.get(a.value) ?? "").localeCompare(cycleStart.get(b.value) ?? "")
        : b.count - a.count || a.label.localeCompare(b.label)
    );

    return { id, label: FACET_LABEL[id], values };
  });
}

// Within a facet the selected values are OR-ed (two outcomes means either);
// across facets they are AND-ed (this outcome AND that scan). An empty facet
// is not a filter.
export function applyFilters(decisions: readonly Decision[], selection: FacetSelection): Decision[] {
  if (selectionSize(selection) === 0) return [...decisions];
  return decisions.filter((d) =>
    FACET_IDS.every((id) => selection[id].size === 0 || selection[id].has(VALUE_OF[id](d)))
  );
}

export const SORT_COLUMNS = [
  { key: "ts_utc", label: "Time (UTC)" },
  { key: "symbol", label: "Symbol" },
  { key: "mode", label: "Mode" },
  { key: "regime", label: "Regime" },
  { key: "action", label: "Action" },
  { key: "gate_reason", label: "Gate outcome" },
  { key: "qty", label: "Qty" },
] as const;

export type SortKey = (typeof SORT_COLUMNS)[number]["key"];

export interface SortState {
  key: SortKey;
  dir: "asc" | "desc";
}

// Newest first, matching the order /decisions returns -- the feed's default
// is "what just happened", and sorting is an override of that, not a
// replacement for it.
export const DEFAULT_SORT: SortState = { key: "ts_utc", dir: "desc" };

function compare(a: Decision, b: Decision, key: SortKey): number {
  if (key === "qty") return (a.qty ?? 0) - (b.qty ?? 0);
  // ts_utc is an ISO-8601 UTC string, so lexicographic order is chronological
  // order -- no Date parsing, and none of its failure modes.
  return String(a[key]).localeCompare(String(b[key]));
}

export function sortDecisions(rows: readonly Decision[], sort: SortState): Decision[] {
  const dir = sort.dir === "asc" ? 1 : -1;
  return [...rows].sort((a, b) => {
    // Rows with no quantity are the overwhelming majority and carry no
    // ordering information, so they sink in BOTH directions rather than
    // filling the first screen with em-dashes on the ascending pass.
    if (sort.key === "qty" && (a.qty === null) !== (b.qty === null)) {
      return a.qty === null ? 1 : -1;
    }
    const cmp = compare(a, b, sort.key) * dir;
    // Ties break on id descending, so equal-valued rows keep the feed's
    // natural newest-first order instead of shuffling between renders.
    return cmp !== 0 ? cmp : b.id - a.id;
  });
}

export interface RejectBar {
  reason: string;
  count: number;
}

// "Every reason the agent refused to trade" -- so APPROVED is not in it. The
// remaining gate_reason values span all three stages that can stop a
// candidate (screen, deliberation, gate), which is exactly the argument: a
// low trade count is not a measure of inactivity, it is a list of rules that
// fired.
export function rejectDistribution(decisions: readonly Decision[]): RejectBar[] {
  const counts = new Map<string, number>();
  for (const d of decisions) {
    if (d.gate_reason === "APPROVED") continue;
    counts.set(d.gate_reason, (counts.get(d.gate_reason) ?? 0) + 1);
  }
  return [...counts]
    .map(([reason, count]) => ({ reason, count }))
    .sort((a, b) => b.count - a.count || a.reason.localeCompare(b.reason));
}

export function enteredCount(decisions: readonly Decision[]): number {
  return decisions.filter((d) => d.action === "ENTER").length;
}

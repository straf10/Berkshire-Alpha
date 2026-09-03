"use client";

import { useState } from "react";
import type { Facet, FacetId, FacetSelection } from "@/lib/decisionFacets";

// Beyond this a facet rolls up behind a "+N more" disclosure. Outcome is the
// facet that needs it -- a session routinely produces a dozen distinct reject
// reasons, and the long tail is one row each.
const MAX_VISIBLE = 6;

function Chip({
  label,
  count,
  pressed,
  onClick,
}: {
  label: string;
  count: number;
  pressed: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      aria-pressed={pressed}
      onClick={onClick}
      className={`inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-xs transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring ${
        pressed
          ? "border-primary bg-primary/10 text-primary"
          : "border-border text-foreground/70 hover:border-foreground/40 hover:text-foreground"
      }`}
    >
      {label}
      <span className={`font-semibold tabular-nums ${pressed ? "text-primary" : "text-foreground"}`}>
        {count}
      </span>
    </button>
  );
}

function FacetRow({
  facet,
  selection,
  onToggle,
}: {
  facet: Facet;
  selection: FacetSelection;
  onToggle: (facet: FacetId, value: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const selected = selection[facet.id];
  if (facet.values.length === 0) return null;

  // A selected value is never hidden behind the disclosure -- a chip you
  // cannot see is a filter you cannot turn off.
  const visible = expanded
    ? facet.values
    : facet.values.filter((v, i) => i < MAX_VISIBLE || selected.has(v.value));
  const hidden = facet.values.length - visible.length;

  return (
    <div role="group" aria-label={facet.label} className="flex flex-wrap items-baseline gap-1.5">
      <span className="w-14 shrink-0 text-xs text-muted-foreground">{facet.label}</span>
      {visible.map((v) => (
        <Chip
          key={v.value}
          label={v.label}
          count={v.count}
          pressed={selected.has(v.value)}
          onClick={() => onToggle(facet.id, v.value)}
        />
      ))}
      {hidden > 0 && (
        <button
          type="button"
          onClick={() => setExpanded(true)}
          className="rounded-md px-1 text-xs text-muted-foreground underline-offset-2 hover:text-foreground hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
        >
          +{hidden} more
        </button>
      )}
      {expanded && facet.values.length > MAX_VISIBLE && (
        <button
          type="button"
          onClick={() => setExpanded(false)}
          className="rounded-md px-1 text-xs text-muted-foreground underline-offset-2 hover:text-foreground hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
        >
          show fewer
        </button>
      )}
    </div>
  );
}

// Real <button aria-pressed> elements, not <div onClick>: a filter is a
// toggle, the keyboard has to reach it, and a screen reader has to be able to
// say which ones are on.
export function FilterChips({
  facets,
  selection,
  onToggle,
  onClear,
  activeCount,
}: {
  facets: Facet[];
  selection: FacetSelection;
  onToggle: (facet: FacetId, value: string) => void;
  onClear: () => void;
  activeCount: number;
}) {
  return (
    <div className="space-y-1.5">
      {facets.map((facet) => (
        <FacetRow key={facet.id} facet={facet} selection={selection} onToggle={onToggle} />
      ))}
      {activeCount > 0 && (
        <button
          type="button"
          onClick={onClear}
          className="rounded-md text-xs text-primary underline-offset-2 hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
        >
          Clear {activeCount} filter{activeCount === 1 ? "" : "s"}
        </button>
      )}
    </div>
  );
}

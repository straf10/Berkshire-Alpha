"use client";

import type { RejectBar } from "@/lib/decisionFacets";
import { REASON_GLOSS } from "@/lib/rejectReasons";

// Five bars, plus a line for the tail. Plain positioned divs -- a five-row
// bar chart does not justify pulling recharts into this part of the bundle,
// and the SSR pass renders the bars rather than an empty container.
const TOP_N = 5;

export function RejectHistogram({
  bars,
  total,
  selected,
  onSelect,
}: {
  bars: RejectBar[];
  /** Rows the distribution was computed over, for the aria-label's wording. */
  total: number;
  selected: ReadonlySet<string>;
  /** Toggles the same Outcome facet the chips drive, so the two stay in step. */
  onSelect: (reason: string) => void;
}) {
  if (bars.length === 0) {
    return <p className="text-sm text-muted-foreground">Nothing was rejected in this window.</p>;
  }

  const top = bars.slice(0, TOP_N);
  const tail = bars.slice(TOP_N);
  const tailCount = tail.reduce((n, b) => n + b.count, 0);
  const max = top[0].count;

  return (
    <div>
      <ul
        role="img"
        aria-label={`Reject reasons across ${total} decisions: ${top
          .map((b) => `${b.reason} ${b.count}`)
          .join(", ")}${tail.length > 0 ? `, and ${tail.length} further reasons totalling ${tailCount}` : ""}.`}
        className="space-y-1.5"
      >
        {top.map((bar) => {
          const on = selected.has(bar.reason);
          const gloss = REASON_GLOSS[bar.reason];
          return (
            <li key={bar.reason}>
              <button
                type="button"
                aria-pressed={on}
                onClick={() => onSelect(bar.reason)}
                className="group w-full rounded-sm px-1 py-0.5 text-left transition-colors hover:bg-muted/40 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
              >
                <span className="flex items-center gap-2">
                  <span
                    className={`min-w-0 flex-1 truncate text-xs ${on ? "font-semibold text-primary" : "text-foreground/80"}`}
                  >
                    {bar.reason}
                  </span>
                  <span className="h-2.5 w-[40%] shrink-0 rounded-sm bg-muted">
                    <span
                      className={`block h-full rounded-sm ${on ? "bg-primary" : "bg-destructive/60"}`}
                      style={{ width: `${(bar.count / max) * 100}%` }}
                    />
                  </span>
                  <span className="w-10 shrink-0 text-right text-xs font-semibold tabular-nums">
                    {bar.count}
                  </span>
                </span>
                {gloss && (
                  <span className="block truncate pr-12 text-[11px] text-muted-foreground">{gloss}</span>
                )}
              </button>
            </li>
          );
        })}
      </ul>
      {tail.length > 0 && (
        <p className="mt-2 px-1 text-caption tabular-nums text-muted-foreground">
          + {tail.length} further reason{tail.length === 1 ? "" : "s"} across {tailCount} decision
          {tailCount === 1 ? "" : "s"} — all of them are in the Outcome chips.
        </p>
      )}
    </div>
  );
}

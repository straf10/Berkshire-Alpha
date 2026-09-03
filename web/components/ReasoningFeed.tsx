"use client";

import { ChevronLeft, ChevronRight, MessagesSquare } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { DataTableSection } from "@/components/DataTableSection";
import { DecisionCard } from "@/components/DecisionCard";
import { FilterChips } from "@/components/FilterChips";
import { RejectHistogram } from "@/components/RejectHistogram";
import { Card, CardContent } from "@/components/ui/card";
import { Table, TableBody, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { apiBase, fetchJson } from "@/lib/api";
import {
  DEFAULT_SORT,
  SORT_COLUMNS,
  applyFilters,
  buildFacets,
  emptySelection,
  enteredCount,
  rejectDistribution,
  selectionSize,
  sortDecisions,
  toggleFacetValue,
  withFacetValues,
  type FacetId,
  type SortKey,
  type SortState,
} from "@/lib/decisionFacets";
import type { Decision, DecisionChain } from "@/lib/types";

const POLL_MS = 15_000;
// The page loads 200 rows (a full session). The poll only has to catch what
// landed in the last 15s, so it asks for a fraction of that and is merged
// into the window rather than replacing it -- otherwise every poll would
// shrink the feed back to 50 rows, or cost 212 KB to avoid doing so.
const POLL_LIMIT = 50;

// The table region gets a fixed height and paginates rather than letting a
// 200-row session push the whole Decisions tab thousands of pixels tall.
const PAGE_SIZE = 25;
const TABLE_HEIGHT = "640px";

// Time and quantity read newest/largest first; the rest are names, which read
// A-Z first.
const DESCENDING_FIRST: ReadonlySet<SortKey> = new Set<SortKey>(["ts_utc", "qty"]);

function mergeById(existing: Decision[], fresh: Decision[]): Decision[] {
  const byId = new Map(existing.map((d) => [d.id, d]));
  for (const d of fresh) byId.set(d.id, d);
  return [...byId.values()].sort((a, b) => b.id - a.id);
}

function PaginationBar({
  page,
  pageCount,
  total,
  onPage,
}: {
  page: number;
  pageCount: number;
  total: number;
  onPage: (page: number) => void;
}) {
  const start = total === 0 ? 0 : page * PAGE_SIZE + 1;
  const end = Math.min((page + 1) * PAGE_SIZE, total);
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border px-3 py-2">
      <span className="text-caption tabular-nums text-muted-foreground">
        {start}–{end} of {total}
      </span>
      <div className="flex items-center gap-1">
        <button
          type="button"
          onClick={() => onPage(page - 1)}
          disabled={page <= 0}
          className="flex items-center gap-1 rounded-md border border-border px-2 py-1 text-xs text-muted-foreground hover:text-foreground disabled:pointer-events-none disabled:opacity-40 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
        >
          <ChevronLeft className="size-3.5" />
          Prev
        </button>
        <span className="px-1 text-caption tabular-nums text-muted-foreground">
          Page {total === 0 ? 0 : page + 1} of {pageCount}
        </span>
        <button
          type="button"
          onClick={() => onPage(page + 1)}
          disabled={page >= pageCount - 1}
          className="flex items-center gap-1 rounded-md border border-border px-2 py-1 text-xs text-muted-foreground hover:text-foreground disabled:pointer-events-none disabled:opacity-40 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
        >
          Next
          <ChevronRight className="size-3.5" />
        </button>
      </div>
    </div>
  );
}

function sessionLabel(decisions: readonly Decision[]): string | null {
  const sessions = [...new Set(decisions.map((d) => d.session_date))].sort();
  if (sessions.length === 0) return null;
  if (sessions.length === 1) return `session ${sessions[0]}`;
  return `sessions ${sessions[0]} → ${sessions[sessions.length - 1]}`;
}

// The centerpiece per docs/plan.md:455 -- "our strongest asset for
// Presentation & Explainability". A plain scannable table whose every row is
// clickable and lazy-fetches its full chain on first expand (DecisionCard),
// fixing the old page's eager N+1 verdict fetch.
//
// Above it, the argument the table is evidence for: this agent refuses far
// more often than it trades, and every refusal names the rule that fired.
// That distribution used to be the sixth column of grey text.
//
// Polls independently of LiveRefresh's 60s full-page router.refresh() (which
// re-runs page.tsx's dozen-odd parallel fetches): this feed only needs the
// one lightweight /decisions endpoint, so it gets its own faster interval
// instead of dragging the whole page's poll down to match and multiplying
// Railway API load for it.
export function ReasoningFeed({
  decisions,
  walkCapFraction,
  initialDecisionId,
  initialGates,
}: {
  decisions: Decision[];
  walkCapFraction: number | null;
  /** From ?decision=<id> -- expand and scroll to that row on load. */
  initialDecisionId?: number | null;
  /** From ?gate=REDUCE_ONLY(,...) -- pre-applied Outcome chips. */
  initialGates?: readonly string[];
}) {
  // null until the first poll lands -- falls back to the SSR-fetched prop
  // until then, and to the merged window after.
  const [polled, setPolled] = useState<Decision[] | null>(null);
  const live = polled ?? decisions;

  // Filtering and sorting are state, not a query: both operate over the rows
  // already in memory. Nothing here refetches.
  const [selection, setSelection] = useState(() =>
    initialGates && initialGates.length > 0
      ? withFacetValues(emptySelection(), "outcome", initialGates)
      : emptySelection()
  );
  const [sort, setSort] = useState<SortState>(DEFAULT_SORT);
  // 0-indexed. Clamped at render time against the current filtered count
  // (below) rather than reset via an effect -- a filter that shrinks the
  // set below the stored page just falls back to the last real page instead
  // of needing a synchronized reset.
  //
  // An in-window ?decision= link (unlike `pinned` below, which only covers
  // OUT-of-window rows) still has to land on the page that actually
  // contains it, or pagination silently hides it -- so the lazy initializer
  // replays the same first-render filter/sort ONCE, over the SSR-fetched
  // `decisions` prop, to find it. A later poll or filter change must not
  // yank the user back to it, which is exactly what a lazy initializer (and
  // not an effect + setState) guarantees.
  const [page, setPage] = useState(() => {
    if (initialDecisionId == null) return 0;
    const initialSelection =
      initialGates && initialGates.length > 0
        ? withFacetValues(emptySelection(), "outcome", initialGates)
        : emptySelection();
    const initialVisible = sortDecisions(applyFilters(decisions, initialSelection), DEFAULT_SORT);
    const idx = initialVisible.findIndex((d) => d.id === initialDecisionId);
    return idx >= 0 ? Math.floor(idx / PAGE_SIZE) : 0;
  });

  // A ?decision= link is meant to survive: the debate worth sharing is not
  // necessarily in the newest 200 rows, and a link that lands on "not here"
  // is worse than no link. When the id is outside the window, fetch that one
  // decision by id and pin it above the feed.
  const [linked, setLinked] = useState<Decision | "missing" | null>(null);
  const inWindow = initialDecisionId != null && live.some((d) => d.id === initialDecisionId);

  useEffect(() => {
    if (initialDecisionId == null) return;
    if (decisions.some((d) => d.id === initialDecisionId)) return;
    let cancelled = false;
    void fetchJson<DecisionChain>(`${apiBase()}/decisions/${initialDecisionId}`).then((chain) => {
      if (!cancelled) setLinked(chain?.decision ?? "missing");
    });
    return () => {
      cancelled = true;
    };
  }, [initialDecisionId, decisions]);

  useEffect(() => {
    const id = setInterval(async () => {
      const fresh = await fetchJson<Decision[]>(`${apiBase()}/decisions?limit=${POLL_LIMIT}`);
      if (fresh) setPolled((prev) => mergeById(prev ?? decisions, fresh));
    }, POLL_MS);
    return () => clearInterval(id);
  }, [decisions]);

  // Counts, facets and the histogram are all derived from the FULL window --
  // never from the filtered rows. A chip whose count reacted to the other
  // chips would fall to zero and disappear the moment you used it.
  const facets = useMemo(() => buildFacets(live, selection), [live, selection]);
  const rejects = useMemo(() => rejectDistribution(live), [live]);
  const entered = useMemo(() => enteredCount(live), [live]);
  const visible = useMemo(
    () => sortDecisions(applyFilters(live, selection), sort),
    [live, selection, sort]
  );

  const pinned = !inWindow && linked !== null && linked !== "missing" ? linked : null;
  // The pinned deep-linked row is always shown, on every page -- it is
  // outside the fetched window by definition (that's why it's pinned), so
  // it can never collide with a page of `visible`. Only `visible` itself is
  // paginated, 25 at a time, inside a fixed-height scroll region.
  const pageCount = Math.max(1, Math.ceil(visible.length / PAGE_SIZE));
  const clampedPage = Math.min(page, pageCount - 1);
  const pageStart = clampedPage * PAGE_SIZE;
  const pageRows = visible.slice(pageStart, pageStart + PAGE_SIZE);
  const rows = pinned ? [pinned, ...pageRows] : pageRows;
  const activeFilters = selectionSize(selection);

  // ?gate= is the shareable half of the deep link: a slide or a demo script
  // can point at "the 28 rows the delta limit stopped" rather than at
  // "scroll down and click around". Written through the History API for the
  // same reason Dashboard writes ?tab= that way -- it must not re-run the
  // page's fetch.
  const gateParam = [...selection.outcome].sort().join(",");
  useEffect(() => {
    const url = new URL(window.location.href);
    if (gateParam) url.searchParams.set("gate", gateParam);
    else url.searchParams.delete("gate");
    const next = `${url.pathname}${url.search}`;
    if (next !== `${window.location.pathname}${window.location.search}`) {
      window.history.replaceState(null, "", next);
    }
  }, [gateParam]);

  // Keep the URL current as rows open and close, so the address bar is always
  // a link to what is on screen -- which is the point of ?decision=.
  function handleToggle(decisionId: number, open: boolean) {
    const url = new URL(window.location.href);
    if (open) url.searchParams.set("decision", String(decisionId));
    else url.searchParams.delete("decision");
    window.history.replaceState(null, "", `${url.pathname}${url.search}`);
  }

  function handleFacetToggle(facet: FacetId, value: string) {
    setSelection((prev) => toggleFacetValue(prev, facet, value));
  }

  function handleSort(key: SortKey) {
    setSort((prev) =>
      prev.key === key
        ? { key, dir: prev.dir === "asc" ? "desc" : "asc" }
        : { key, dir: DESCENDING_FIRST.has(key) ? "desc" : "asc" }
    );
  }

  let banner: string | null = null;
  if (pinned) {
    banner = `Decision ${initialDecisionId} is from ${pinned.session_date}, outside the ${live.length} most recent rows below — pinned to the top so the link still works.`;
  } else if (!inWindow && linked === "missing") {
    banner = `Decision ${initialDecisionId} could not be found.`;
  }

  const session = sessionLabel(live);

  return (
    <>
      {live.length > 0 && (
        <Card>
          <CardContent className="pt-6">
            <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
              <h2 className="text-subheadline font-semibold uppercase tracking-wide text-muted-foreground">
                Every reason the agent refused to trade
              </h2>
              <p className="text-caption tabular-nums text-muted-foreground">
                {live.length} decisions{session ? ` · ${session}` : ""} · filtered client-side
              </p>
            </div>
            <p className="mt-1 text-2xl font-semibold tabular-nums">
              {entered}{" "}
              <span className="text-lg font-normal text-muted-foreground">
                of {live.length} entered
              </span>
            </p>
            <p aria-live="polite" className="text-caption tabular-nums text-muted-foreground">
              {activeFilters > 0
                ? `Showing ${visible.length} of ${live.length} rows — ${activeFilters} filter${activeFilters === 1 ? "" : "s"} on.`
                : "Every row below is one candidate the agent looked at and wrote a reason for."}
            </p>

            <div className="mt-4 grid gap-4 md:grid-cols-2">
              <div>
                <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                  Reject distribution
                </h3>
                <RejectHistogram
                  bars={rejects}
                  total={live.length}
                  selected={selection.outcome}
                  onSelect={(reason) => handleFacetToggle("outcome", reason)}
                />
              </div>
              <div>
                <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                  Filters
                </h3>
                <FilterChips
                  facets={facets}
                  selection={selection}
                  onToggle={handleFacetToggle}
                  onClear={() => setSelection(emptySelection())}
                  activeCount={activeFilters}
                />
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      <DataTableSection
        icon={MessagesSquare}
        title="Reasoning feed"
        isEmpty={rows.length === 0}
        emptyMessage={
          activeFilters > 0 ? "No decisions match these filters." : "No decisions yet."
        }
        aside={
          banner ? (
            <p className="mb-2 rounded-md border border-warn/30 bg-warn/10 px-2 py-1 text-xs text-warn">
              {banner}
            </p>
          ) : undefined
        }
        scrollHeight={TABLE_HEIGHT}
        footer={
          visible.length > 0 && (
            <PaginationBar page={clampedPage} pageCount={pageCount} total={visible.length} onPage={setPage} />
          )
        }
      >
        <Table className="min-w-[820px]">
          <TableHeader>
            <TableRow>
              {SORT_COLUMNS.map((col) => {
                const active = sort.key === col.key;
                return (
                  <TableHead
                    key={col.key}
                    aria-sort={active ? (sort.dir === "asc" ? "ascending" : "descending") : "none"}
                  >
                    <button
                      type="button"
                      onClick={() => handleSort(col.key)}
                      className="flex items-center gap-1 rounded-sm hover:text-primary focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
                    >
                      {col.label}
                      <span aria-hidden className={active ? "text-primary" : "text-muted-foreground/60"}>
                        {active ? (sort.dir === "asc" ? "▲" : "▼") : "↕"}
                      </span>
                    </button>
                  </TableHead>
                );
              })}
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((d) => (
              <DecisionCard
                key={d.id}
                decision={d}
                walkCapFraction={walkCapFraction}
                defaultOpen={d.id === initialDecisionId}
                onToggle={handleToggle}
              />
            ))}
          </TableBody>
        </Table>
      </DataTableSection>
    </>
  );
}

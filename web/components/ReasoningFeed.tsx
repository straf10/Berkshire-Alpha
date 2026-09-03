"use client";

import { MessagesSquare } from "lucide-react";
import { useEffect, useState } from "react";
import { DataTableSection } from "@/components/DataTableSection";
import { DecisionCard } from "@/components/DecisionCard";
import { Table, TableBody, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { apiBase, fetchJson } from "@/lib/api";
import type { Decision, DecisionChain } from "@/lib/types";

const POLL_MS = 15_000;
// The page loads 200 rows (a full session). The poll only has to catch what
// landed in the last 15s, so it asks for a fraction of that and is merged
// into the window rather than replacing it -- otherwise every poll would
// shrink the feed back to 50 rows, or cost 212 KB to avoid doing so.
const POLL_LIMIT = 50;

function mergeById(existing: Decision[], fresh: Decision[]): Decision[] {
  const byId = new Map(existing.map((d) => [d.id, d]));
  for (const d of fresh) byId.set(d.id, d);
  return [...byId.values()].sort((a, b) => b.id - a.id);
}

// The centerpiece per docs/plan.md:455 -- "our strongest asset for
// Presentation & Explainability". A plain scannable table whose every row is
// clickable and lazy-fetches its full chain on first expand (DecisionCard),
// fixing the old page's eager N+1 verdict fetch.
//
// Polls independently of LiveRefresh's 60s full-page router.refresh() (which
// re-runs page.tsx's 14 parallel fetches): this feed only needs the one
// lightweight /decisions endpoint, so it gets its own faster interval
// instead of dragging the whole page's poll down to match and multiplying
// Railway API load 14x for it.
export function ReasoningFeed({
  decisions,
  walkCapFraction,
  initialDecisionId,
}: {
  decisions: Decision[];
  walkCapFraction: number | null;
  /** From ?decision=<id> -- expand and scroll to that row on load. */
  initialDecisionId?: number | null;
}) {
  // null until the first poll lands -- falls back to the SSR-fetched prop
  // until then, and to the merged window after.
  const [polled, setPolled] = useState<Decision[] | null>(null);
  const live = polled ?? decisions;

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

  const pinned = !inWindow && linked !== null && linked !== "missing" ? linked : null;
  const rows = pinned ? [pinned, ...live] : live;

  useEffect(() => {
    const id = setInterval(async () => {
      const fresh = await fetchJson<Decision[]>(`${apiBase()}/decisions?limit=${POLL_LIMIT}`);
      if (fresh) setPolled((prev) => mergeById(prev ?? decisions, fresh));
    }, POLL_MS);
    return () => clearInterval(id);
  }, [decisions]);

  // Keep the URL current as rows open and close, so the address bar is always
  // a link to what is on screen -- which is the point of ?decision=. Written
  // through the History API directly, matching Dashboard's ?tab= handling, so
  // it never re-runs the page's fetch.
  function handleToggle(decisionId: number, open: boolean) {
    const url = new URL(window.location.href);
    if (open) url.searchParams.set("decision", String(decisionId));
    else url.searchParams.delete("decision");
    window.history.replaceState(null, "", `${url.pathname}${url.search}`);
  }

  let banner: string | null = null;
  if (pinned) {
    banner = `Decision ${initialDecisionId} is from ${pinned.session_date}, outside the ${live.length} most recent rows below — pinned to the top so the link still works.`;
  } else if (!inWindow && linked === "missing") {
    banner = `Decision ${initialDecisionId} could not be found.`;
  }

  return (
    <DataTableSection
      icon={MessagesSquare}
      title="Reasoning feed"
      isEmpty={rows.length === 0}
      emptyMessage="No decisions yet."
      aside={
        banner ? (
          <p className="mb-2 rounded-md border border-amber-500/30 bg-amber-500/10 px-2 py-1 text-xs text-amber-400">
            {banner}
          </p>
        ) : undefined
      }
    >
      <Table className="min-w-[820px]">
        <TableHeader>
          <TableRow>
            <TableHead>Time (UTC)</TableHead>
            <TableHead>Symbol</TableHead>
            <TableHead>Mode</TableHead>
            <TableHead>Regime</TableHead>
            <TableHead>Action</TableHead>
            <TableHead>Gate outcome</TableHead>
            <TableHead>Qty</TableHead>
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
  );
}

"use client";

import { MessagesSquare } from "lucide-react";
import { useEffect, useState } from "react";
import { DataTableSection } from "@/components/DataTableSection";
import { DecisionCard } from "@/components/DecisionCard";
import { Table, TableBody, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { apiBase, fetchJson } from "@/lib/api";
import type { Decision } from "@/lib/types";

const POLL_MS = 15_000;

// The centerpiece per PLAN.md: "our strongest asset for Presentation &
// Explainability" -- same table shape as DecisionsLog (same columns, same
// DataTableSection wrapper), but each row is clickable and lazy-fetches its
// full chain on first expand (DecisionCard), fixing the old page's eager
// N+1 verdict fetch.
//
// Polls independently of LiveRefresh's 60s full-page router.refresh() (which
// re-runs page.tsx's 14 parallel fetches): this feed only needs the one
// lightweight /decisions endpoint, so it gets its own faster interval
// instead of dragging the whole page's poll down to match and multiplying
// Railway API load 14x for it.
export function ReasoningFeed({ decisions }: { decisions: Decision[] }) {
  // null until the first poll lands -- falls back to the SSR-fetched prop
  // until then, and to itself after (the 15s poll is always as fresh or
  // fresher than the page's 60s refresh, so once it's running there's
  // nothing useful left to sync back from `decisions`).
  const [polled, setPolled] = useState<Decision[] | null>(null);
  const live = polled ?? decisions;

  useEffect(() => {
    const id = setInterval(async () => {
      const fresh = await fetchJson<Decision[]>(`${apiBase()}/decisions?limit=50`);
      if (fresh) setPolled(fresh);
    }, POLL_MS);
    return () => clearInterval(id);
  }, []);

  return (
    <DataTableSection
      icon={MessagesSquare}
      title="Reasoning feed"
      isEmpty={live.length === 0}
      emptyMessage="No decisions yet."
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
          {live.map((d) => (
            <DecisionCard key={d.id} decision={d} />
          ))}
        </TableBody>
      </Table>
    </DataTableSection>
  );
}

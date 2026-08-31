import { MessagesSquare } from "lucide-react";
import { DataTableSection } from "@/components/DataTableSection";
import { DecisionCard } from "@/components/DecisionCard";
import { Table, TableBody, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import type { Decision } from "@/lib/types";

// The centerpiece per PLAN.md: "our strongest asset for Presentation &
// Explainability" -- same table shape as DecisionsLog (same columns, same
// DataTableSection wrapper), but each row is clickable and lazy-fetches its
// full chain on first expand (DecisionCard), fixing the old page's eager
// N+1 verdict fetch.
export function ReasoningFeed({ decisions }: { decisions: Decision[] }) {
  return (
    <DataTableSection
      icon={MessagesSquare}
      title="Reasoning feed"
      isEmpty={decisions.length === 0}
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
          {decisions.map((d) => (
            <DecisionCard key={d.id} decision={d} />
          ))}
        </TableBody>
      </Table>
    </DataTableSection>
  );
}

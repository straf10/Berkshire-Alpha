import { ScrollText } from "lucide-react";
import { DataTableSection } from "@/components/DataTableSection";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { actionColor, modeLabel } from "@/lib/format";
import type { Decision } from "@/lib/types";

// The compact log kept above the reasoning feed (docs/day6_ui_plan.md S1.6) --
// same data the page has always shown, no per-row verdict fetch here anymore;
// that detail now lives in ReasoningFeed's lazily-loaded DecisionCard.
export function DecisionsLog({ decisions }: { decisions: Decision[] }) {
  return (
    <DataTableSection icon={ScrollText} title="Decisions log" isEmpty={decisions.length === 0} emptyMessage="No decisions yet.">
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
            <TableRow key={d.id}>
              <TableCell className="whitespace-nowrap text-foreground/70">{d.ts_utc}</TableCell>
              <TableCell className="font-semibold">{d.symbol}</TableCell>
              <TableCell>{modeLabel(d.mode)}</TableCell>
              <TableCell>{d.regime}</TableCell>
              <TableCell className={`font-semibold ${actionColor(d.action)}`}>{d.action}</TableCell>
              <TableCell className="text-foreground/70">{d.gate_reason}</TableCell>
              <TableCell>{d.qty ?? "—"}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </DataTableSection>
  );
}

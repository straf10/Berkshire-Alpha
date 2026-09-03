import { Layers } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { DataTableSection } from "@/components/DataTableSection";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { compactLegs, daysToExpiry } from "@/lib/format";
import { TONE_CLASS, TONE_VARIANT, tradeOutcome } from "@/lib/tradeStatus";
import type { AssignmentEvent, OpenPosition } from "@/lib/types";

function netGreeks(liveLegs: { qty: number; delta: number; vega: number }[]): { delta: number; vega: number } | null {
  if (liveLegs.length === 0) return null;
  return liveLegs.reduce(
    (acc, l) => ({ delta: acc.delta + l.delta * l.qty, vega: acc.vega + l.vega * l.qty }),
    { delta: 0, vega: 0 }
  );
}

export function OpenPositionsTable({
  positions,
  assignments,
}: {
  positions: OpenPosition[] | null;
  assignments: AssignmentEvent[];
}) {
  if (positions === null || positions.length === 0) return null;

  const assignedSymbols = new Set(assignments.map((a) => a.symbol));

  return (
    <DataTableSection icon={Layers} title="Open positions">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Symbol</TableHead>
            <TableHead>Structure</TableHead>
            <TableHead>Legs</TableHead>
            <TableHead>Qty</TableHead>
            <TableHead>Expiry</TableHead>
            <TableHead>DTE</TableHead>
            <TableHead>Fill</TableHead>
            <TableHead>Net Δ / vega</TableHead>
            <TableHead>Status</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {positions.map((p) => {
            const g = netGreeks(p.live_legs);
            // Same vocabulary as TradeHistoryTable rather than the raw enum --
            // two tables on one tab printing FILLED and "Filled" reads as a bug.
            const outcome = tradeOutcome(p);
            return (
              <TableRow key={p.id}>
                <TableCell className="font-semibold">
                  {p.symbol}
                  {assignedSymbols.has(p.symbol) && (
                    <Badge variant="destructive" className="ml-2">
                      assigned
                    </Badge>
                  )}
                </TableCell>
                <TableCell>{p.structure}</TableCell>
                <TableCell>{compactLegs(p.legs_json)}</TableCell>
                <TableCell>{p.filled_qty || p.qty}</TableCell>
                <TableCell>{p.expiry}</TableCell>
                <TableCell>{daysToExpiry(p.expiry)}</TableCell>
                <TableCell>{p.fill_price?.toFixed(2) ?? "—"}</TableCell>
                <TableCell className="text-foreground/70">
                  {g ? `${g.delta.toFixed(2)} / ${g.vega.toFixed(2)}` : "—"}
                </TableCell>
                <TableCell>
                  <Badge
                    variant={TONE_VARIANT[outcome.tone]}
                    className={TONE_CLASS[outcome.tone]}
                    title={outcome.tip}
                  >
                    {outcome.label}
                  </Badge>
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </DataTableSection>
  );
}

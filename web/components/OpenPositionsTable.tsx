import { Layers } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { compactLegs, daysToExpiry } from "@/lib/format";
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
    <div className="mb-6">
      <p className="mb-2 flex items-center gap-1.5 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
        <Layers className="size-3.5" />
        Open positions
      </p>
      <div className="overflow-x-auto rounded-md border border-border">
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
                  <TableCell className="text-foreground/70">{p.status}</TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}

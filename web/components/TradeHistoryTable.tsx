import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { compactLegs, formatSignedMoney } from "@/lib/format";
import type { Trade } from "@/lib/types";

function statusVariant(t: Trade): "default" | "destructive" | "secondary" {
  if (t.reject_code) return "destructive";
  if (t.status === "FILLED") return "default";
  return "secondary";
}

export function TradeHistoryTable({ trades }: { trades: Trade[] | null }) {
  if (trades === null || trades.length === 0) return null;

  return (
    <div className="mb-6">
      <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        Trade history
      </p>
      <div className="overflow-x-auto rounded-md border border-border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Time (UTC)</TableHead>
              <TableHead>Symbol</TableHead>
              <TableHead>Structure</TableHead>
              <TableHead>Legs</TableHead>
              <TableHead>Qty filled</TableHead>
              <TableHead>Walk steps</TableHead>
              <TableHead>Fill</TableHead>
              <TableHead>Realized P&amp;L</TableHead>
              <TableHead>Status</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {trades.map((t) => (
              <TableRow key={t.id}>
                <TableCell className="whitespace-nowrap text-foreground/70">{t.ts_utc}</TableCell>
                <TableCell className="font-semibold">{t.symbol}</TableCell>
                <TableCell>{t.structure}</TableCell>
                <TableCell>{compactLegs(t.legs_json)}</TableCell>
                <TableCell>{t.filled_qty || "—"}</TableCell>
                <TableCell>{t.walk_steps}</TableCell>
                <TableCell>{t.fill_price?.toFixed(2) ?? "—"}</TableCell>
                <TableCell
                  className={
                    t.realized_pnl == null
                      ? "text-foreground/70"
                      : t.realized_pnl >= 0
                        ? "text-emerald-400"
                        : "text-red-400"
                  }
                >
                  {t.realized_pnl != null ? formatSignedMoney(t.realized_pnl) : t.closed_at ? "—" : "open"}
                </TableCell>
                <TableCell>
                  <Badge variant={statusVariant(t)}>{t.reject_code ?? t.status}</Badge>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}

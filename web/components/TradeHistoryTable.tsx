import { History } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { DataTableSection } from "@/components/DataTableSection";
import { SectionEmpty } from "@/components/SectionEmpty";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { compactLegs, formatDateTime, formatSignedMoney } from "@/lib/format";
import {
  OUTCOME_LEGEND,
  POSITION_TIP,
  TONE_CLASS,
  TONE_VARIANT,
  positionOutcome,
  tradeOutcome,
} from "@/lib/tradeStatus";
import type { Trade } from "@/lib/types";

// Visible, keyboard-reachable counterpart to the per-badge `title` tooltips --
// native title text never appears on touch, and "Cancelled at price cap" is
// the one label that most needs explaining: it is the agent obeying its own
// walk cap, not a broker rejection.
function OutcomeLegend() {
  return (
    <details className="mb-3 text-sm">
      <summary className="cursor-pointer text-muted-foreground hover:text-foreground">
        What these outcomes mean
      </summary>
      <ul className="mt-2 space-y-1.5 border-l border-border/60 pl-3">
        {OUTCOME_LEGEND.map((row) => (
          <li key={row.label} className="flex flex-wrap items-baseline gap-2">
            <Badge variant={TONE_VARIANT[row.tone]} className={TONE_CLASS[row.tone]}>
              {row.label}
            </Badge>
            <span className="text-foreground/70">{row.tip}</span>
          </li>
        ))}
      </ul>
    </details>
  );
}

export function TradeHistoryTable({ trades }: { trades: Trade[] | null }) {
  if (trades === null || trades.length === 0) {
    return (
      <SectionEmpty
        icon={History}
        title="Trade history"
        reason="No orders sent yet. Every order the agent submits lands here with its limit walk and its outcome; until one passes the risk gate, the Decisions tab is where the reason lives."
      />
    );
  }

  return (
    <DataTableSection icon={History} title="Trade history" aside={<OutcomeLegend />}>
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
            {/* Order outcome and position outcome are separate questions --
                "did the order work" vs "is there a position". Merging them is
                what previously printed the word "open" in Realized P&L, where
                it collided with the Status column's own vocabulary. */}
            <TableHead>Order outcome</TableHead>
            <TableHead>Position</TableHead>
            <TableHead>Realized P&amp;L</TableHead>
            <TableHead>Exit reason</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {trades.map((t) => {
            const outcome = tradeOutcome(t);
            const position = positionOutcome(t);
            return (
              <TableRow key={t.id}>
                <TableCell className="whitespace-nowrap text-foreground/70">{formatDateTime(t.ts_utc)}</TableCell>
                <TableCell className="font-semibold">{t.symbol}</TableCell>
                <TableCell>{t.structure}</TableCell>
                <TableCell>{compactLegs(t.legs_json)}</TableCell>
                <TableCell>{t.filled_qty || "—"}</TableCell>
                <TableCell>{t.walk_steps}</TableCell>
                <TableCell>{t.fill_price?.toFixed(2) ?? "—"}</TableCell>
                <TableCell>
                  <Badge
                    variant={TONE_VARIANT[outcome.tone]}
                    className={TONE_CLASS[outcome.tone]}
                    title={outcome.tip}
                  >
                    {outcome.label}
                  </Badge>
                </TableCell>
                <TableCell className="whitespace-nowrap text-foreground/70" title={POSITION_TIP[position]}>
                  {position}
                </TableCell>
                {/* Only ever a number or an em-dash now. */}
                <TableCell
                  className={
                    t.realized_pnl == null
                      ? "text-foreground/70"
                      : t.realized_pnl >= 0
                        ? "text-emerald-400"
                        : "text-red-400"
                  }
                >
                  {t.realized_pnl != null ? formatSignedMoney(t.realized_pnl) : "—"}
                </TableCell>
                <TableCell className="text-foreground/70">{t.exit_reason ?? "—"}</TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </DataTableSection>
  );
}

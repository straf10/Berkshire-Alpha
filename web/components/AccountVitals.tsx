import { Wallet } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EquitySparkline } from "@/components/charts/EquitySparkline";
import { formatMoney, formatSignedMoney } from "@/lib/format";
import type { AccountState, EquityPoint } from "@/lib/types";

function dayPnl(equity: number, history: EquityPoint[], sessionDate: string | undefined): number | null {
  if (!sessionDate) return null;
  const todays = history.filter((p) => p.ts_utc.startsWith(sessionDate));
  if (todays.length === 0) return null;
  return equity - todays[0].equity;
}

export function AccountVitals({
  account,
  history,
  sessionDate,
}: {
  account: AccountState | null;
  history: EquityPoint[] | null;
  sessionDate: string | undefined;
}) {
  if (account === null) return null;

  const equity = account.equity ? Number(account.equity) : null;
  const pnl = equity !== null && history ? dayPnl(equity, history, sessionDate) : null;

  return (
    <Card className="mb-6">
      <CardHeader className="pb-2">
        <CardTitle className="flex items-center gap-1.5 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
          <Wallet className="size-3.5" />
          Account
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <div className="text-2xl font-semibold tabular-nums">
              {equity !== null ? formatMoney(equity) : "—"}
            </div>
            <div className={`text-base tabular-nums ${pnl === null ? "text-muted-foreground" : pnl >= 0 ? "text-emerald-400" : "text-red-400"}`}>
              {pnl === null ? "day P&L —" : `${formatSignedMoney(pnl)} today`}
            </div>
            <div className="mt-2 flex gap-4 text-sm text-muted-foreground">
              <span>Buying power {account.buying_power ? formatMoney(account.buying_power) : "—"}</span>
              <span>Cash {account.cash ? formatMoney(account.cash) : "—"}</span>
            </div>
          </div>
          <div className="min-w-[220px] flex-1">
            {history && history.length > 0 ? (
              <EquitySparkline points={history} />
            ) : (
              <div className="flex h-16 items-center text-sm text-muted-foreground">No equity history yet.</div>
            )}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

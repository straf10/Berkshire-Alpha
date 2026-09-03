import { Wallet } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EquitySparkline } from "@/components/charts/EquitySparkline";
import { SectionEmpty } from "@/components/SectionEmpty";
import { formatMoney, formatPct, formatSignedMoney } from "@/lib/format";
import type { AccountState, EquityPoint } from "@/lib/types";

type DayPnl =
  | { value: number; pct: number; basis: string }
  | { reason: string };

// Alpaca's own previous-close equity (`last_equity`, written alongside
// `equity` at agent/main.py:1158 and :1209) is the basis a broker statement
// uses, so it is the primary reading.
//
// The snapshot method -- diff against the first /equity/history row whose
// ts_utc starts with status.session_date -- is kept as the fallback but must
// NOT be primary: while the market is closed, session_date is already the
// NEXT session (agent/session.py's current_or_next_session returns the
// upcoming session plan when the clock says closed), so no equity row ever
// matches and the card printed a bare "day P&L —" every evening with
// last_equity sitting unused two fields away. It also returned null before
// the session's first tick and whenever the 500-row window didn't reach today.
//
// Never returns a bare dash: if neither basis is available, it says which one
// it is waiting for.
function dayPnl(
  account: AccountState,
  history: EquityPoint[] | null,
  sessionDate: string | undefined
): DayPnl {
  const equity = account.equity ? Number(account.equity) : NaN;
  if (!Number.isFinite(equity)) return { reason: "no equity reported yet" };

  const prevClose = account.last_equity ? Number(account.last_equity) : NaN;
  if (Number.isFinite(prevClose) && prevClose !== 0) {
    const value = equity - prevClose;
    return { value, pct: value / prevClose, basis: `vs previous close ${formatMoney(prevClose)}` };
  }

  // `last_equity` is only refreshed when a cycle runs, so overnight it
  // correctly holds the previous session's close and the number stays right
  // until the next session updates it -- that's the intended behaviour, not
  // staleness. This branch is for a feed that never reported it at all.
  const sessionOpen = sessionDate
    ? (history ?? []).find((p) => p.ts_utc.startsWith(sessionDate))?.equity
    : undefined;
  if (sessionOpen !== undefined && sessionOpen !== 0) {
    const value = equity - sessionOpen;
    return {
      value,
      pct: value / sessionOpen,
      basis: `vs this session's first sample ${formatMoney(sessionOpen)}`,
    };
  }

  return { reason: "no previous close reported yet" };
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
  if (account === null) {
    return (
      <SectionEmpty
        icon={Wallet}
        title="Account"
        reason={
          <>
            No account snapshot yet. The agent writes <code>state/account</code> from the broker on
            its first management tick, which runs within five minutes of the market opening.
          </>
        }
      />
    );
  }

  const equity = account.equity ? Number(account.equity) : null;
  const pnl = dayPnl(account, history, sessionDate);
  const known = "value" in pnl;
  const sign = known && pnl.value >= 0 ? "text-emerald-400" : "text-red-400";

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
            {known ? (
              <>
                <div className={`flex flex-wrap items-baseline gap-x-2 text-base tabular-nums ${sign}`}>
                  <span>{formatSignedMoney(pnl.value)}</span>
                  <span className="text-sm">
                    {pnl.value > 0 && "+"}
                    {formatPct(pnl.pct, 2)}
                  </span>
                  <span className="text-sm text-muted-foreground">today</span>
                </div>
                <div className="text-[11px] text-muted-foreground">{pnl.basis}</div>
              </>
            ) : (
              <div className="text-base text-muted-foreground">day P&amp;L — {pnl.reason}</div>
            )}
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

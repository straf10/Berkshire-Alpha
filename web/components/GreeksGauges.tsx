import { Activity } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { LimitMeter } from "@/components/LimitMeter";
import { SectionEmpty } from "@/components/SectionEmpty";
import { formatDateTime } from "@/lib/format";
import type { GreeksSnapshot } from "@/lib/types";

export function GreeksGauges({ snapshot }: { snapshot: GreeksSnapshot | null }) {
  if (snapshot === null) {
    return (
      <SectionEmpty
        icon={Activity}
        title="Portfolio greeks"
        className=""
        reason="No greeks snapshot yet. The management tick re-prices the book every five minutes while the market is open, and writes the aggregate delta and vega against their limits."
      />
    );
  }

  const breached = snapshot.breached === 1;

  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between gap-2 pb-2">
        <CardTitle className="flex items-center gap-1.5 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
          <Activity className="size-3.5" />
          Portfolio greeks
        </CardTitle>
        {breached && <Badge variant="destructive">REDUCE-ONLY</Badge>}
      </CardHeader>
      <CardContent className="space-y-4">
        <LimitMeter label="Delta" value={snapshot.delta_dollars} limit={snapshot.delta_limit} />
        <LimitMeter label="Vega" value={snapshot.vega_dollars} limit={snapshot.vega_limit} compact />
        {breached && (
          /* The badge on its own reads as "something broke". It is the
             opposite: agent/main.py:1206 writes reduce_only the moment the
             aggregate breaches, and gates.py:137 then rejects new plans at
             REDUCE_ONLY -- while the same management_tick goes straight on to
             exit_tick() and assignment reconciliation. Say so, next to it. */
          <p className="rounded-md border-l-2 border-destructive/60 bg-destructive/5 py-2 pl-2.5 pr-2 text-xs leading-relaxed text-foreground/80">
            <span className="font-semibold text-foreground">Reduce-only blocks new entries only.</span>{" "}
            Exits, assignment reconciliation and the 5-minute management tick keep running — a new
            spread is rejected at <code className="text-foreground/90">REDUCE_ONLY</code> before it is
            ever priced.
          </p>
        )}
        <p className="text-[11px] text-muted-foreground">
          As of {formatDateTime(snapshot.ts_utc, { seconds: true })} — the tick on each track is the
          limit; anything past it is a breach.
        </p>
      </CardContent>
    </Card>
  );
}

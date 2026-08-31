import { Activity } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { GreeksBarChart } from "@/components/charts/GreeksBarChart";
import { formatDateTime, formatMoney } from "@/lib/format";
import type { GreeksSnapshot } from "@/lib/types";

export function GreeksGauges({ snapshot }: { snapshot: GreeksSnapshot | null }) {
  if (snapshot === null) return null;

  const deltaPct = (Math.abs(snapshot.delta_dollars) / snapshot.delta_limit) * 100;
  const vegaPct = (Math.abs(snapshot.vega_dollars) / snapshot.vega_limit) * 100;

  const data = [
    {
      name: "Delta",
      pctOfLimit: deltaPct,
      raw: `${formatMoney(snapshot.delta_dollars)} of ±${formatMoney(snapshot.delta_limit)}`,
    },
    {
      name: "Vega",
      pctOfLimit: vegaPct,
      raw: `${formatMoney(snapshot.vega_dollars)} of ±${formatMoney(snapshot.vega_limit)}`,
    },
  ];

  return (
    <Card className="mb-6">
      <CardHeader className="flex-row items-center justify-between pb-2">
        <CardTitle className="flex items-center gap-1.5 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
          <Activity className="size-3.5" />
          Portfolio greeks
        </CardTitle>
        {snapshot.breached === 1 && <Badge variant="destructive">REDUCE-ONLY</Badge>}
      </CardHeader>
      <CardContent>
        <GreeksBarChart data={data} />
        <p className="mt-1 text-[11px] text-muted-foreground">
          As of {formatDateTime(snapshot.ts_utc, { seconds: true })} — bars show % of the portfolio delta/vega limit consumed.
        </p>
      </CardContent>
    </Card>
  );
}

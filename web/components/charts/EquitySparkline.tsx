"use client";

import { CartesianGrid, Line, LineChart } from "recharts";
import { ChartContainer, ChartTooltip, ChartTooltipContent, type ChartConfig } from "@/components/ui/chart";
import { formatDateTime } from "@/lib/format";
import type { EquityPoint } from "@/lib/types";

const chartConfig = {
  equity: { label: "Equity", color: "var(--chart-1)" },
} satisfies ChartConfig;

// Same treatment as WalkTimelineChart -- a plain line with a dashed grid,
// not a gradient-filled area. The area fill implied a "zero" baseline that
// isn't meaningful for equity (it never actually approaches zero), and
// invited reading its shaded mass as a magnitude the way a bar chart's does.
export function EquitySparkline({ points }: { points: EquityPoint[] }) {
  if (points.length < 2) {
    return <div className="flex h-16 items-center text-sm text-muted-foreground">Not enough history yet.</div>;
  }

  // A sparkline is the one chart on this page with no axis labels at all, so
  // without this it is literally unreadable to anyone not looking at it.
  const first = points[0];
  const last = points[points.length - 1];
  const change = last.equity - first.equity;
  const money = (n: number) => `$${Math.round(n).toLocaleString()}`;

  return (
    <ChartContainer
      config={chartConfig}
      role="img"
      aria-label={`Account equity over ${points.length} samples: ${money(first.equity)} on ${formatDateTime(first.ts_utc)} to ${money(last.equity)} on ${formatDateTime(last.ts_utc)}, ${change >= 0 ? "up" : "down"} ${money(Math.abs(change))}.`}
      className="aspect-auto h-16 w-full font-mono tabular-nums"
    >
      <LineChart data={points} margin={{ top: 4, right: 4, bottom: 0, left: 4 }}>
        <CartesianGrid strokeDasharray="2 4" vertical={false} />
        <ChartTooltip
          cursor={false}
          content={
            <ChartTooltipContent
              labelFormatter={(_, payload) => {
                const ts = payload?.[0]?.payload?.ts_utc as string | undefined;
                return ts ?? "";
              }}
              formatter={(value) => [`$${Number(value).toLocaleString()}`, "Equity"]}
            />
          }
        />
        <Line
          type="monotone"
          dataKey="equity"
          stroke="var(--chart-1)"
          strokeWidth={1.5}
          dot={false}
          isAnimationActive={false}
        />
      </LineChart>
    </ChartContainer>
  );
}

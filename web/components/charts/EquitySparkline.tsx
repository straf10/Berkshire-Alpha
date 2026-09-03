"use client";

import { Area, AreaChart, CartesianGrid } from "recharts";
import { ChartContainer, ChartTooltip, ChartTooltipContent, type ChartConfig } from "@/components/ui/chart";
import { formatDateTime } from "@/lib/format";
import type { EquityPoint } from "@/lib/types";

const chartConfig = {
  equity: { label: "Equity", color: "var(--chart-1)" },
} satisfies ChartConfig;

// A gradient-filled area, by explicit design direction -- an earlier pass
// argued the fill's shaded mass reads as a magnitude against a "zero"
// baseline that isn't meaningful for equity, and used a plain line instead.
// The fill here is deliberately kept faint enough at the top (opacity 0.35)
// that it stays a mood rather than a quantity.
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
      <AreaChart data={points} margin={{ top: 4, right: 4, bottom: 0, left: 4 }}>
        <defs>
          <linearGradient id="equity-fill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor="var(--chart-1)" stopOpacity={0.35} />
            <stop offset="95%" stopColor="var(--chart-1)" stopOpacity={0} />
          </linearGradient>
        </defs>
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
        <Area
          type="monotone"
          dataKey="equity"
          stroke="var(--chart-1)"
          strokeWidth={2}
          fill="url(#equity-fill)"
          dot={false}
          isAnimationActive={false}
        />
      </AreaChart>
    </ChartContainer>
  );
}

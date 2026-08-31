"use client";

import { Area, AreaChart } from "recharts";
import { ChartContainer, ChartTooltip, ChartTooltipContent, type ChartConfig } from "@/components/ui/chart";
import type { EquityPoint } from "@/lib/types";

const chartConfig = {
  equity: { label: "Equity", color: "var(--chart-1)" },
} satisfies ChartConfig;

export function EquitySparkline({ points }: { points: EquityPoint[] }) {
  if (points.length < 2) {
    return <div className="flex h-16 items-center text-sm text-muted-foreground">Not enough history yet.</div>;
  }

  return (
    <ChartContainer config={chartConfig} className="aspect-auto h-16 w-full">
      <AreaChart data={points} margin={{ top: 4, right: 4, bottom: 0, left: 4 }}>
        <defs>
          <linearGradient id="equityFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--chart-1)" stopOpacity={0.4} />
            <stop offset="100%" stopColor="var(--chart-1)" stopOpacity={0.02} />
          </linearGradient>
        </defs>
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
          strokeWidth={1.5}
          fill="url(#equityFill)"
          isAnimationActive={false}
        />
      </AreaChart>
    </ChartContainer>
  );
}

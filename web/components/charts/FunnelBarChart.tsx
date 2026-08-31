"use client";

import { Bar, BarChart, XAxis, YAxis } from "recharts";
import { ChartContainer, ChartTooltip, ChartTooltipContent, type ChartConfig } from "@/components/ui/chart";

interface StageDatum {
  name: string;
  count: number;
  dropReason: string | null;
}

const chartConfig = {
  count: { label: "Count", color: "var(--chart-2)" },
} satisfies ChartConfig;

export function FunnelBarChart({ data }: { data: StageDatum[] }) {
  return (
    <ChartContainer config={chartConfig} className="aspect-auto h-32 w-full">
      <BarChart data={data} layout="vertical" margin={{ left: 8, right: 24, top: 4, bottom: 4 }}>
        <XAxis type="number" hide />
        <YAxis type="category" dataKey="name" width={84} tickLine={false} axisLine={false} />
        <ChartTooltip
          cursor={false}
          content={
            <ChartTooltipContent
              formatter={(value, _name, item) => [
                `${value}${item.payload.dropReason ? ` — dropped mostly on ${item.payload.dropReason}` : ""}`,
                "",
              ]}
              hideLabel
            />
          }
        />
        <Bar dataKey="count" fill="var(--chart-2)" radius={4} isAnimationActive={false} />
      </BarChart>
    </ChartContainer>
  );
}

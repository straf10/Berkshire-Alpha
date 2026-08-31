"use client";

import { Bar, BarChart, Cell, XAxis, YAxis } from "recharts";
import { ChartContainer, ChartTooltip, ChartTooltipContent, type ChartConfig } from "@/components/ui/chart";

interface GaugeDatum {
  name: string;
  pctOfLimit: number; // 0-100+, can exceed 100 on breach
  raw: string; // formatted "$x,xxx of $y,yyy" for the tooltip
}

const chartConfig = {
  pctOfLimit: { label: "% of limit" },
} satisfies ChartConfig;

function colorFor(pct: number): string {
  if (pct >= 100) return "var(--chart-5)"; // red
  if (pct >= 70) return "var(--chart-4)"; // amber
  return "var(--chart-3)"; // green
}

export function GreeksBarChart({ data }: { data: GaugeDatum[] }) {
  return (
    <ChartContainer config={chartConfig} className="aspect-auto h-24 w-full">
      <BarChart data={data} layout="vertical" margin={{ left: 8, right: 24, top: 4, bottom: 4 }}>
        <XAxis type="number" domain={[0, (max: number) => Math.max(100, max)]} hide />
        <YAxis type="category" dataKey="name" width={44} tickLine={false} axisLine={false} />
        <ChartTooltip
          cursor={false}
          content={
            <ChartTooltipContent
              formatter={(_value, _name, item) => [item.payload.raw, ""]}
              hideLabel
            />
          }
        />
        <Bar dataKey="pctOfLimit" radius={4} isAnimationActive={false}>
          {data.map((d) => (
            <Cell key={d.name} fill={colorFor(d.pctOfLimit)} />
          ))}
        </Bar>
      </BarChart>
    </ChartContainer>
  );
}

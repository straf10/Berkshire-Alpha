"use client";

import { Line, LineChart, ReferenceDot, ReferenceLine, XAxis, YAxis } from "recharts";
import { ChartContainer, ChartTooltip, ChartTooltipContent, type ChartConfig } from "@/components/ui/chart";
import { safeJsonParse } from "@/lib/format";
import type { Trade, WalkEvent } from "@/lib/types";

const chartConfig = {
  limit: { label: "Limit price" },
} satisfies ChartConfig;

interface WalkTimelineChartProps {
  trade: Trade;
  natural: number | null; // decision.plan_json.net_natural -- the "natural" (worst-of-bid/ask) reference line
  // agent config's WALK_CAP_FRACTION (fetched live from /config, never hardcoded here) --
  // lets this draw the pre-P0-2/P0-3 unclamped reference line for illustration
  // without re-implementing walk_cap()'s clamp logic in TypeScript.
  walkCapFraction: number | null;
}

interface StepPoint {
  step: number;
  limit: number;
}

// Recharts calls axis tickFormatters during layout passes with placeholder/
// undefined values, not just real data points -- guard rather than let one
// of those calls throw.
const money = (v: number | undefined) => (typeof v === "number" && Number.isFinite(v) ? `$${v.toFixed(2)}` : "—");

// docs/review.md Task 4: plots each WalkEvent's limit (y) against its step
// (x) as a step-line, with mid/natural/cap reference lines. `cap` is never
// recomputed here -- it comes straight from trade.walk_cap, which
// agent/storage/read.py computes by calling the SAME walk_cap() the live
// walk calls (agent/tools/walk_cap.py), so this chart and the live walk can
// never disagree.
export function WalkTimelineChart({ trade, natural, walkCapFraction }: WalkTimelineChartProps) {
  const events = safeJsonParse<WalkEvent[]>(trade.events_json) ?? [];
  const points: StepPoint[] = events
    .filter((e) => e.limit !== null)
    .map((e) => ({ step: e.step, limit: Number(e.limit) }));

  // 0-1 walk steps has nothing worth plotting as a timeline -- but returning
  // null rendered *nothing at all* where a chart belongs, and half of the
  // production trades filled at the submitted limit, so half of the expanded
  // rows had a silent hole in them. Say what happened instead.
  if (points.length < 2) {
    const only = points[0];
    return (
      <p className="mt-2 text-sm text-muted-foreground">
        {trade.fill_price !== null
          ? `Filled at the submitted limit${only ? ` of ${money(only.limit)}` : ""} — no walk was needed.`
          : `No walk to plot — the order ${trade.status.toLowerCase()} at the submitted limit${only ? ` of ${money(only.limit)}` : ""}.`}
      </p>
    );
  }

  const mid = trade.submitted_limit;
  const cap = trade.walk_cap ?? null;
  const lastStep = points[points.length - 1].step;
  const fillPoint = trade.fill_price !== null ? { step: lastStep, limit: trade.fill_price } : null;

  // The pre-P0-2/P0-3 formula, unclamped: mid + WALK_CAP_FRACTION*(natural -
  // mid). That linear term never changed -- the fix only ADDED the width
  // clamp on top of it -- so this is not a re-implementation of walk_cap()'s
  // decision logic, just the one line of arithmetic that predates the clamp,
  // using the fraction as reported by /config today. Shown only when it
  // actually differs from the live (clamped) cap, so an ordinary in-band
  // trade the clamp never touched doesn't get a redundant second line.
  const legacyCap =
    natural !== null && walkCapFraction !== null ? mid + walkCapFraction * (natural - mid) : null;
  const showLegacyCap = legacyCap !== null && cap !== null && Math.abs(legacyCap - cap) > 0.005;

  // Recharts' YAxis domain="auto" only scales to the Line's own data --
  // it does NOT stretch to fit ReferenceLine values, so mid/natural/cap
  // sitting outside the walked price range would render clipped or missing
  // entirely. Compute the domain by hand over every value actually drawn.
  const allValues = [
    ...points.map((p) => p.limit),
    mid,
    cap,
    natural,
    legacyCap,
    fillPoint?.limit,
  ].filter((v): v is number => typeof v === "number" && Number.isFinite(v));
  const rawMin = Math.min(...allValues);
  const rawMax = Math.max(...allValues);
  const pad = Math.max((rawMax - rawMin) * 0.08, 0.05);
  const yDomain: [number, number] = [rawMin - pad, rawMax + pad];

  return (
    <div className="mt-2">
      <ChartContainer
        config={chartConfig}
        role="img"
        aria-label={`Limit-order walk for ${trade.symbol}: ${lastStep} step${lastStep === 1 ? "" : "s"} from ${money(mid)} to ${money(points[points.length - 1].limit)}${cap !== null ? `, against a cap of ${money(cap)}` : ""}${natural !== null ? ` and a natural of ${money(natural)}` : ""} — ${
          trade.fill_price !== null ? `filled at ${money(trade.fill_price)}` : `unfilled, ${trade.status}`
        }.`}
        className="aspect-auto h-44 w-full"
      >
        <LineChart data={points} margin={{ top: 10, right: 16, bottom: 4, left: 4 }}>
          <XAxis
            dataKey="step"
            type="number"
            domain={[0, lastStep]}
            tickLine={false}
            axisLine={false}
            label={{ value: "walk step", position: "insideBottom", offset: -2, fontSize: 11 }}
          />
          <YAxis
            dataKey="limit"
            type="number"
            domain={yDomain}
            tickLine={false}
            axisLine={false}
            width={56}
            tickFormatter={(v: number) => money(v)}
          />
          <ChartTooltip
            cursor={false}
            content={
              <ChartTooltipContent
                labelFormatter={(_, payload) => `step ${payload?.[0]?.payload?.step ?? ""}`}
                formatter={(value) => [money(Number(value)), "limit"]}
              />
            }
          />
          {/* Colour by CATEGORY, not by chart slot. mid and natural are both
              MARKET reference points, so they share one neutral hue and are
              told apart by their labels; the cap is the agent's own
              discipline threshold, so it is --warn; the pre-fix cap is
              history, so it is --idle; the walked line and the fill are the
              agent acting, so they are --primary. Previously these were
              chart-2/3/4/5 in the order they were added. */}
          <ReferenceLine
            y={mid}
            stroke="var(--muted-foreground)"
            strokeDasharray="4 2"
            label={{
              value: `mid ${money(mid)}`,
              position: "insideTopLeft",
              fontSize: 10,
              fill: "var(--muted-foreground)",
            }}
          />
          {natural !== null && (
            <ReferenceLine
              y={natural}
              stroke="var(--muted-foreground)"
              strokeDasharray="1 3"
              label={{
                value: `natural ${money(natural)}`,
                position: "insideTopLeft",
                fontSize: 10,
                fill: "var(--muted-foreground)",
              }}
            />
          )}
          {cap !== null && (
            <ReferenceLine
              y={cap}
              stroke="var(--warn)"
              label={{ value: `cap ${money(cap)}`, position: "insideBottomLeft", fontSize: 10, fill: "var(--warn)" }}
            />
          )}
          {showLegacyCap && legacyCap !== null && (
            <ReferenceLine
              y={legacyCap}
              stroke="var(--idle)"
              strokeDasharray="2 2"
              label={{
                value: `pre-fix cap ${money(legacyCap)}`,
                position: "insideBottomLeft",
                fontSize: 10,
                fill: "var(--idle)",
              }}
            />
          )}
          <Line
            type="stepAfter"
            dataKey="limit"
            stroke="var(--primary)"
            strokeWidth={1.5}
            dot={false}
            isAnimationActive={false}
          />
          {fillPoint && <ReferenceDot x={fillPoint.step} y={fillPoint.limit} r={4} fill="var(--pos)" stroke="none" />}
        </LineChart>
      </ChartContainer>
    </div>
  );
}

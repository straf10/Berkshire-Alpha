"use client";

import { Route } from "lucide-react";
import { useEffect, useState } from "react";
import { Section, SectionHero } from "@/components/Section";
import { SectionEmpty } from "@/components/SectionEmpty";
import { Skeleton } from "@/components/ui/skeleton";
import { WalkTimelineChart } from "@/components/charts/WalkTimelineChart";
import { apiBase, fetchJson } from "@/lib/api";
import { formatDateTime, safeJsonParse } from "@/lib/format";
import type { DecisionChain, Trade } from "@/lib/types";

interface SpreadPlanShape {
  net_natural: string;
}

function money(v: number | null | undefined): string {
  return typeof v === "number" && Number.isFinite(v) ? `$${v.toFixed(2)}` : "—";
}

function Fact({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3 border-b border-hairline py-1 last:border-b-0">
      <span className="text-sm text-muted-foreground">{label}</span>
      <span className={`font-semibold tabular-nums ${tone ?? ""}`}>{value}</span>
    </div>
  );
}

// The walk-timeline chart draws real `events_json` rows against a cap that
// agent/storage/read.py computes by calling the SAME walk_cap() the live walk
// calls -- so the chart and the execution engine cannot disagree. That is the
// single best piece of execution evidence on the dashboard, and it was
// reachable only by expanding a decision row and scrolling.
//
// The featured trade is `max(walk_steps)`, computed, never a hard-coded id,
// so it survives every new session.
export function FeaturedWalk({
  trades,
  walkCapFraction,
}: {
  trades: Trade[] | null;
  walkCapFraction: number | null;
}) {
  const featured =
    trades && trades.length > 0
      ? trades.reduce((best, t) => (t.walk_steps > best.walk_steps ? t : best))
      : null;
  const hasWalk = featured !== null && featured.walk_steps > 0;
  const decisionId = hasWalk ? featured.decision_id : null;

  // `walk_cap` and the plan's `net_natural` are only on the chain endpoint --
  // /trades has neither (types.ts:123-127). One extra request, for one trade.
  const [chain, setChain] = useState<DecisionChain | null>(null);
  // Seeded from `hasWalk`, so the skeleton is on screen from the first paint
  // and the effect only ever reports the result -- it never has to setState
  // synchronously on mount.
  const [loading, setLoading] = useState(hasWalk);

  useEffect(() => {
    if (decisionId === null) return;
    let cancelled = false;
    void fetchJson<DecisionChain>(`${apiBase()}/decisions/${decisionId}`).then((data) => {
      if (cancelled) return;
      setChain(data);
      setLoading(false);
    });
    return () => {
      cancelled = true;
    };
  }, [decisionId]);

  if (!hasWalk) {
    return (
      <SectionEmpty
        icon={Route}
        title="How the agent actually gets filled"
        reason={
          trades && trades.length > 0
            ? "No order has needed a limit walk yet — every fill so far landed at the submitted limit. The first order that has to walk toward the natural will be drawn here, step by step."
            : "No orders sent yet. When one is, the limit walk it took to get filled — every replacement, against the cap the live walk itself computed — is drawn here."
        }
      />
    );
  }

  // Prefer the chain's copy of the trade: it is the only one carrying walk_cap.
  const trade = chain?.trades.find((t) => t.id === featured.id) ?? featured;
  const plan = chain?.decision ? safeJsonParse<SpreadPlanShape>(chain.decision.plan_json) : null;
  const natural = plan ? Number(plan.net_natural) : null;
  const cap = trade.walk_cap ?? null;
  const filled = trade.fill_price !== null;

  // Whether today's rule would have stopped this walk. Stated as a fact about
  // the cap, never as "this trade broke a rule" -- the cap is recomputed now,
  // and a trade that predates the clamp did not violate anything at the time.
  const endPrice = trade.fill_price ?? trade.final_limit;
  const capWouldHaveStopped = cap !== null && endPrice !== null && endPrice > cap;

  // The companion argument, computed rather than asserted: how often the agent
  // walked away instead of paying up.
  const cancelledAtCap = (trades ?? []).filter((t) => t.reject_code === "UNFILLED_REJECT").length;

  return (
    <Section
      icon={Route}
      title="How the agent actually gets filled"
      meta={
        <>
          {trade.symbol} · {formatDateTime(trade.ts_utc)} · the same code path the live walk runs
        </>
      }
    >
      <SectionHero
        value={trade.walk_steps.toLocaleString()}
        suffix={`limit replacements · ${trade.structure.replaceAll("_", " ").toLowerCase()}`}
      />
      <div className="mt-4 grid gap-x-6 gap-y-4 md:grid-cols-2">
        <div className="min-w-0">
          {loading ? (
            <Skeleton className="h-44 w-full" />
          ) : (
            <WalkTimelineChart trade={trade} natural={natural} walkCapFraction={walkCapFraction} />
          )}
        </div>
        <div className="min-w-0">
          <p className="mb-3 text-sm text-foreground/80">
            {trade.walk_steps.toLocaleString()} limit replacements, one step apart, walked from the
            mid toward the natural — real <code>events_json</code> rows written by the order manager,
            not a reconstruction of them. The cap drawn on the chart is not recomputed here: it comes
            from <code>trade.walk_cap</code>, which the API produces by calling the identical
            function the live walk calls.
          </p>
          <div>
            <Fact label="Submitted at mid" value={money(trade.submitted_limit)} />
            <Fact label="Final limit" value={money(trade.final_limit)} />
            {natural !== null && Number.isFinite(natural) && (
              <Fact label="Natural (worst of bid/ask)" value={money(natural)} />
            )}
            <Fact
              label={filled ? "Filled" : "Outcome"}
              value={filled ? money(trade.fill_price) : trade.status}
              tone={filled ? "text-pos" : "text-warn"}
            />
            {cap !== null && (
              <Fact label="Cap under today's rule" value={money(cap)} tone="text-warn" />
            )}
          </div>
          {capWouldHaveStopped && (
            <p className="mt-3 text-[11px] text-muted-foreground">
              This order ran <em>before</em> the walk-cap clamp landed. Under today&apos;s rule it
              would have cancelled at {money(cap)} rather than reaching {money(endPrice)} — which is
              what the agent now does:{" "}
              <span className="font-semibold text-foreground/80">
                {cancelledAtCap} of {trades?.length ?? 0}
              </span>{" "}
              orders in the table below were cancelled at the cap instead of filled at a worse price.
            </p>
          )}
        </div>
      </div>
    </Section>
  );
}

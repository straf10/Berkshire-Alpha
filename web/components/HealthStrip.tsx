import { Activity } from "lucide-react";
import type { HealthSample } from "@/lib/types";

// One bar per health_samples row (one per management_tick, ~5min apart while
// the market's open), oldest first, left to right -- a persisted uptime
// history like a status-page strip, not a client-side poll, so every visitor
// sees the same real history and it survives a page refresh.
export function HealthStrip({ samples }: { samples: HealthSample[] | null }) {
  if (samples === null || samples.length === 0) return null;

  const upCount = samples.filter((s) => s.ok === 1).length;
  const uptimePct = ((upCount / samples.length) * 100).toFixed(1);

  return (
    <div className="mb-6">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-1.5">
        <p className="flex items-center gap-1.5 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
          <Activity className="size-3.5" />
          Agent uptime
        </p>
        <span className="text-sm text-foreground/70">{uptimePct}% up, last {samples.length} checks</span>
      </div>
      <div className="flex h-6 w-full gap-px overflow-hidden rounded-md">
        {samples.map((s, i) => (
          <div
            key={`${s.ts_utc}-${i}`}
            title={`${s.ts_utc} — ${s.ok === 1 ? "up" : "down"}`}
            className={`h-full flex-1 ${s.ok === 1 ? "bg-emerald-500/70" : "bg-red-500/80"}`}
          />
        ))}
      </div>
    </div>
  );
}

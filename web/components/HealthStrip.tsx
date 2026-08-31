import { Activity } from "lucide-react";
import type { HealthBucket } from "@/lib/types";

const STATUS_CLASS: Record<HealthBucket["status"], string> = {
  up: "bg-emerald-500/70",
  down: "bg-red-500/80",
  no_data: "bg-muted",
};

const STATUS_LABEL: Record<HealthBucket["status"], string> = {
  up: "up",
  down: "down",
  no_data: "no data (market closed)",
};

// One thin bar per hour bucket (agent/storage/read.py's health_history),
// oldest first left to right -- a status-page-style strip (status.cursor.com
// pattern) scoped to the last ~90 hours instead of 90 days, since this agent
// has run days not months. no_data hours (market closed -- management_tick
// only runs while open) render as a neutral gray, never red, so closed
// overnight/weekend hours don't read as incidents.
export function HealthStrip({ buckets }: { buckets: HealthBucket[] | null }) {
  if (buckets === null || buckets.length === 0) return null;

  const withData = buckets.filter((b) => b.status !== "no_data");
  const upCount = withData.filter((b) => b.status === "up").length;
  const uptimePct = withData.length > 0 ? ((upCount / withData.length) * 100).toFixed(1) : "—";
  const noDataCount = buckets.length - withData.length;

  return (
    <div className="mb-6">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-1.5">
        <p className="flex items-center gap-1.5 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
          <Activity className="size-3.5" />
          Agent uptime
        </p>
        <span className="text-sm text-foreground/70">
          {uptimePct}% up over last {buckets.length}h ({withData.length} checked, {noDataCount}h no data)
        </span>
      </div>
      <div className="flex h-7 w-full gap-[2px]">
        {buckets.map((b) => (
          <div
            key={b.bucket_start_utc}
            title={`${b.bucket_start_utc} — ${STATUS_LABEL[b.status]}${
              b.total_count > 0 ? ` (${b.ok_count}/${b.total_count} checks)` : ""
            }`}
            className={`h-full flex-1 rounded-sm ${STATUS_CLASS[b.status]}`}
          />
        ))}
      </div>
      <div className="mt-1 flex justify-between text-[11px] text-muted-foreground">
        <span>{buckets.length}h ago</span>
        <span>now</span>
      </div>
    </div>
  );
}

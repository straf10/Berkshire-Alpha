import { Activity } from "lucide-react";
import { SectionEmpty } from "@/components/SectionEmpty";
import { formatDateTime } from "@/lib/format";
import { isMarketHour } from "@/lib/marketHours";
import type { HealthBucket, Status } from "@/lib/types";

const STATUS_CLASS: Record<HealthBucket["status"], string> = {
  up: "bg-pos/70",
  down: "bg-neg/80",
  no_data: "bg-warn/70",
};

const STATUS_LABEL: Record<HealthBucket["status"], string> = {
  up: "checks passed",
  down: "a check failed",
  no_data: "no health sample in this market hour",
};

// One thin bar per hour, status-page style -- but over MARKET hours only.
//
// The old strip painted all 90 wall-clock hours and rendered 71 of them grey,
// because health_samples are only written inside management_tick and
// management_tick only runs while the market is open (agent/main.py:1213,
// trading_loop). So a closed hour was structurally "no data": the strip read
// as a mostly-dead service with "100.0% up" floating over it, which looks
// like either a broken widget or spin. Neither is true -- the agent was idle
// exactly when it was designed to be idle.
//
// Two rules make it honest and green at the same time:
//   1. Closed hours are not drawn at all. An hour the agent was never meant
//      to run in is not uptime data.
//   2. The window starts at the first health sample. Market hours before the
//      agent was first deployed are "not running yet", not "down" -- the same
//      convention every status page uses.
// Everything left is a market hour the agent was live for, so a gap here is a
// real gap and paints amber, and a failed check paints red.
export function HealthStrip({ buckets, status }: { buckets: HealthBucket[] | null; status: Status }) {
  if (buckets === null || buckets.length === 0) {
    return (
      <SectionEmpty
        icon={Activity}
        title="Agent uptime — market hours"
        reason="No health samples yet. The management tick writes one every five minutes while the market is open; this strip draws one bar per market hour from the first sample onwards."
      />
    );
  }

  const marketHours = buckets.filter((b) => isMarketHour(b.bucket_start_utc, status.open_utc, status.close_utc));
  const firstSample = marketHours.findIndex((b) => b.total_count > 0);
  if (firstSample === -1) {
    return (
      <SectionEmpty
        icon={Activity}
        title="Agent uptime — market hours"
        reason="No health sample has landed inside a market hour yet. Closed hours are not drawn at all — an hour the agent was never meant to run in is not uptime data — so the strip appears with the first sample after the opening bell."
      />
    );
  }

  const covered = marketHours.slice(firstSample);
  const upCount = covered.filter((b) => b.status === "up").length;
  const failedChecks = covered.reduce((sum, b) => sum + (b.total_count - b.ok_count), 0);
  const gaps = covered.filter((b) => b.status === "no_data").length;
  const uptimePct = ((upCount / covered.length) * 100).toFixed(1);

  return (
    <div>
      <div className="mb-2 flex flex-wrap items-center justify-between gap-1.5">
        <p className="flex items-center gap-1.5 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
          <Activity className="size-3.5" />
          Agent uptime — market hours
        </p>
        <span className="text-sm text-foreground/70">
          {uptimePct}% up over {covered.length} market hours ({failedChecks} failed{" "}
          {failedChecks === 1 ? "check" : "checks"}
          {gaps > 0 && `, ${gaps}h with no sample`})
        </span>
      </div>
      {/* role="img" with the reading spelled out: the per-bar detail is
          otherwise only in a native `title`, which never appears on touch and
          is not announced reliably. The summary line above carries the same
          numbers as real text, so nothing here is the sole source of anything
          -- the label just gives the bars themselves a non-visual equivalent. */}
      <div
        role="img"
        aria-label={`Uptime by market hour since ${formatDateTime(covered[0].bucket_start_utc)}: ${upCount} of ${covered.length} hours passed every check, ${failedChecks} failed check${failedChecks === 1 ? "" : "s"}, ${gaps} hour${gaps === 1 ? "" : "s"} with no sample.`}
        className="flex h-7 w-full gap-[2px]"
      >
        {covered.map((b) => (
          <div
            key={b.bucket_start_utc}
            title={`${formatDateTime(b.bucket_start_utc)} — ${STATUS_LABEL[b.status]}${
              b.total_count > 0 ? ` (${b.ok_count}/${b.total_count} checks)` : ""
            }`}
            className={`h-full flex-1 rounded-sm ${STATUS_CLASS[b.status]}`}
          />
        ))}
      </div>
      <div className="mt-1 flex flex-wrap justify-between gap-x-3 text-[11px] text-muted-foreground">
        <span>{formatDateTime(covered[0].bucket_start_utc)} — first health sample</span>
        {/* The legend only appears when there is something to explain: with a
            clean run the strip is one colour and needs no key. */}
        {(gaps > 0 || failedChecks > 0) && (
          <span className="flex items-center gap-3">
            {gaps > 0 && (
              <span className="flex items-center gap-1">
                <span className="size-2 rounded-sm bg-warn/70" />
                no sample
              </span>
            )}
            {failedChecks > 0 && (
              <span className="flex items-center gap-1">
                <span className="size-2 rounded-sm bg-neg/80" />
                check failed
              </span>
            )}
          </span>
        )}
        <span>now</span>
      </div>
    </div>
  );
}

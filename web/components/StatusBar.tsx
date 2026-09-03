"use client";

import { BrainCircuit, Clock, OctagonAlert } from "lucide-react";
import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { formatCountdown, formatTimeUtc } from "@/lib/format";
import type { Status } from "@/lib/types";

// `next_action` is a loop-internal label, not copy: agent/main.py's
// _next_action() returns "market open" / "entry scan N" / "management tick",
// and agent/tests/test_main.py asserts those exact strings. So they are
// translated here rather than renamed at the source. Anything unmapped falls
// through to the raw string, so a future backend label degrades to the old
// wording instead of rendering blank.
const ACTION_COPY: Record<string, { label: string; hint: string }> = {
  "market open": { label: "the opening bell", hint: "opens the session and queues its entry scans" },
  "management tick": { label: "position check", hint: "re-price the greeks, test every exit rule" },
};

function actionCopy(nextAction: string): { label: string; hint: string } {
  const scan = /^entry scan (\d+)$/.exec(nextAction);
  if (scan) return { label: `entry scan ${scan[1]}`, hint: "hunt for new trades" };
  return ACTION_COPY[nextAction] ?? { label: nextAction, hint: "" };
}

// The session schedule the backend has always published and the UI has never
// shown: one dot per entry-scan slot, filled up to completed_scans. "Scan 2 of
// 4 done" is the cheapest available proof that this runs on its own clock.
function SessionSchedule({ status }: { status: Status }) {
  const scans = status.scan_utcs ?? [];
  if (scans.length === 0) return null;

  const done = status.completed_scans ?? 0;
  const window =
    status.open_utc && status.close_utc
      ? `session ${formatTimeUtc(status.open_utc)}–${formatTimeUtc(status.close_utc)} UTC`
      : null;

  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted-foreground">
      {window && <span>{window}</span>}
      <span
        className="flex items-center gap-1.5"
        title={scans.map((t, i) => `entry scan ${i + 1} — ${formatTimeUtc(t)} UTC`).join("\n")}
      >
        <span className="flex items-center gap-1" aria-hidden>
          {scans.map((t, i) => (
            <span
              key={t}
              className={`size-2 rounded-full ${
                i < done ? "bg-emerald-400" : "border border-muted-foreground/50"
              }`}
            />
          ))}
        </span>
        {done === 0 ? `${scans.length} entry scans queued` : `scan ${done} of ${scans.length} done`}
      </span>
    </div>
  );
}

export function StatusBar({ status }: { status: Status }) {
  // Lazy initializer (not a synchronous setState-in-effect) so the first
  // render already has a real value; the effect only subscribes to the
  // ticking interval, which is the pattern react-hooks/set-state-in-effect
  // allows. Server and client render at slightly different instants, so the
  // countdown span is allowed to mismatch on hydration rather than warn.
  //
  // Client component at all because the countdown would otherwise freeze at
  // the value computed when the server rendered the page, going stale behind
  // any caching/CDN hop. Ticks locally from the server-provided ISO stamps.
  const [now, setNow] = useState<number>(() => Date.now());

  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(id);
  }, []);

  const known = status.next_action !== undefined && status.next_action_utc !== undefined;
  const live = status.live === true;
  const copy = known ? actionCopy(status.next_action!) : null;

  return (
    <div className="mb-6 flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-3 text-base">
        <Badge
          variant="outline"
          className={`gap-1.5 ${
            live
              ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-400"
              : "border-amber-500/30 bg-amber-500/10 text-amber-400"
          }`}
        >
          <span className={`h-1.5 w-1.5 rounded-full ${live ? "bg-emerald-400" : "bg-amber-400"}`} />
          {live ? "LIVE" : "DRY-RUN"}
        </Badge>
        {status.llm_enabled !== undefined && (
          <span className="text-muted-foreground">
            <BrainCircuit className="mr-1 inline size-3.5 align-[-2px]" />
            LLM {status.llm_enabled ? "on" : "off"}
          </span>
        )}
        {/* Published by the backend since the pre-market hardening pass and
            typed nowhere until now -- a halt nobody can see is a halt nobody
            can clear. */}
        {status.entries_halted && (
          <Badge
            variant="outline"
            className="gap-1.5 border-red-500/30 bg-red-500/10 text-red-400"
            title="New entries are blocked for the rest of this session. Position management and exits still run."
          >
            <OctagonAlert className="size-3" />
            ENTRIES HALTED
          </Badge>
        )}
        {known && copy ? (
          <span className="text-foreground/80">
            <Clock className="mr-1 inline size-3.5 align-[-2px]" />
            {status.is_open ? "market open" : "market closed"} — next: {copy.label} in{" "}
            <span className="font-semibold text-primary whitespace-nowrap" suppressHydrationWarning>
              {formatCountdown(status.next_action_utc!, now)}
            </span>
            {copy.hint && <span className="text-muted-foreground"> · {copy.hint}</span>}
          </span>
        ) : (
          <span className="text-muted-foreground">status unavailable</span>
        )}
      </div>
      <SessionSchedule status={status} />
    </div>
  );
}

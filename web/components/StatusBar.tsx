"use client";

import { BrainCircuit, CalendarOff, OctagonAlert } from "lucide-react";
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
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm tabular-nums text-muted-foreground">
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
                i < done ? "bg-primary" : "border border-muted-foreground/50"
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
  // The one thing that honestly means "the loop is live right now" -- drives
  // the ambient dot below. Not a fabricated heartbeat: no field distinguishes
  // "scanning" from "executing" mid-cycle, so the dot doesn't pretend to.
  const active = status.is_open === true;

  return (
    <div className="flex flex-col gap-2.5">
      {/* Ambient state, not decoration -- entries_halted is a fail-safe an
          operator has to clear, not a routine notice, so while it holds it
          is the loudest thing on this page: full-width, its own glow, ahead
          of every other status element. --warn, not --neg -- this is a risk
          state the agent is obeying, not a P&L sign or a failure. */}
      {status.entries_halted && (
        <div className="flex items-start gap-2.5 rounded-lg border border-warn/40 bg-warn/10 px-3 py-2.5 shadow-[0_0_28px_-10px_var(--warn)]">
          <span className="relative mt-0.5 flex size-2.5 shrink-0">
            <span className="absolute inset-0 animate-ping rounded-full bg-warn opacity-75 motion-reduce:hidden" />
            <span className="relative block size-2.5 rounded-full bg-warn" />
          </span>
          <div className="min-w-0">
            <p className="flex items-center gap-1.5 text-subheadline uppercase tracking-wide text-warn">
              <OctagonAlert className="size-3.5" />
              Entries halted
            </p>
            <p className="mt-0.5 text-caption text-foreground/80">
              New entries are blocked for the rest of this session. Position management and exits still
              run.
            </p>
          </div>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-3 text-base">
        <Badge
          variant="outline"
          className={`gap-1.5 ${
            live
              ? "border-pos/30 bg-pos/10 text-pos"
              : "border-idle/40 bg-idle/10 text-muted-foreground"
          }`}
        >
          <span className={`h-1.5 w-1.5 rounded-full ${live ? "bg-pos" : "bg-idle"}`} />
          {live ? "LIVE" : "DRY-RUN"}
        </Badge>
        {status.llm_enabled !== undefined && (
          <span className="text-muted-foreground">
            <BrainCircuit className="mr-1 inline size-3.5 align-[-2px]" />
            LLM {status.llm_enabled ? "on" : "off"}
          </span>
        )}
        {/* The planned, dated stand-down on the final session
            (agent/config.py FREEZE_ENTRIES_FROM), distinct from the halt
            above: that one is a fail-safe an operator has to clear, this one
            is a decision the agent was built to make. Shown separately for
            the same reason the halt is shown at all -- a stand-down nobody
            can see reads as an agent that has stopped working. */}
        {status.entries_frozen && (
          <Badge
            variant="outline"
            className="gap-1.5 border-hairline bg-surface-2 text-foreground/80"
            title="Final session: no new entries. The book has to be flat before the horizon, so a 3-7 DTE entry opened now would be a round trip with none of the horizon it was sized for. Exits and position management still run."
          >
            <CalendarOff className="size-3" />
            ENTRIES FROZEN
          </Badge>
        )}
        {known && copy ? (
          <span className="flex items-center gap-1.5 text-foreground/80">
            {/* Ambient loop indicator, not ornament: pulses only while
                status.is_open is true -- the one field that actually means
                the trading loop is live right now. Dim and static the rest
                of the time, same as the market it tracks. */}
            <span className="relative flex size-2 shrink-0" aria-hidden>
              {active && (
                <span className="absolute inset-0 animate-ping rounded-full bg-primary opacity-75 motion-reduce:hidden" />
              )}
              <span
                className={`relative block size-2 rounded-full ${
                  active ? "bg-primary shadow-[0_0_6px_var(--primary)]" : "bg-idle"
                }`}
              />
            </span>
            {status.is_open ? "market open" : "market closed"} — next: {copy.label} in{" "}
            <span className="font-semibold tabular-nums text-primary whitespace-nowrap" suppressHydrationWarning>
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

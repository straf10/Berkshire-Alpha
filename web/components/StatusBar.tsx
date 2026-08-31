"use client";

import { BrainCircuit, Clock } from "lucide-react";
import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { formatCountdown } from "@/lib/format";
import type { Status } from "@/lib/types";

// Client component: the countdown otherwise freezes at the value computed
// when the server rendered the page (docs/day6_ui_plan.md S2), which goes
// stale immediately behind any caching/CDN hop. Ticks locally from the
// server-provided ISO timestamps instead.
export function StatusBar({ status }: { status: Status }) {
  // Lazy initializer (not a synchronous setState-in-effect) so the first
  // render already has a real value; the effect only subscribes to the
  // ticking interval, which is the pattern react-hooks/set-state-in-effect
  // allows. Server and client render at slightly different instants, so the
  // countdown span is allowed to mismatch on hydration rather than warn.
  const [now, setNow] = useState<number>(() => Date.now());

  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(id);
  }, []);

  const known = status.next_action !== undefined && status.next_action_utc !== undefined;
  const live = status.live === true;

  return (
    <div className="mb-6 flex flex-wrap items-center gap-3 text-base">
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
      {known ? (
        <span className="text-foreground/80">
          <Clock className="mr-1 inline size-3.5 align-[-2px]" />
          {status.is_open ? "market open" : "market closed"} — next: {status.next_action} in{" "}
          <span className="font-semibold text-primary whitespace-nowrap" suppressHydrationWarning>
            {formatCountdown(status.next_action_utc!, now)}
          </span>
        </span>
      ) : (
        <span className="text-muted-foreground">status unavailable</span>
      )}
    </div>
  );
}

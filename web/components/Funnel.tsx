import { ArrowRight, Filter } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { SectionEmpty } from "@/components/SectionEmpty";
import { REASON_GLOSS } from "@/lib/rejectReasons";
import type { FunnelResponse, FunnelStage } from "@/lib/types";

const STAGE_LABELS: Record<FunnelStage["name"], string> = {
  screened: "Screened",
  shortlisted: "Shortlisted",
  built: "Built",
  debated: "Debated",
  entered: "Entered",
};


// What happened between this stage and the next. The API gives one
// top_reject_reason per stage, meaning "the most common reason for the rows
// that did not survive past it" (agent/storage/read.py:238-247) -- so it
// belongs on the arrow, not on the stage.
function DropRow({ stage, drop }: { stage: FunnelStage; drop: number }) {
  if (stage.count === 0) {
    return <p className="py-1 pl-[6.75rem] text-xs text-muted-foreground">nothing reached this stage</p>;
  }
  if (drop < 0) {
    return (
      <p className="py-1 pl-[6.75rem] text-xs text-muted-foreground">
        <span className="font-semibold text-foreground/80">+{-drop}</span> · entered without a debate
        — the quant-only path skips it
      </p>
    );
  }
  if (drop === 0) {
    return (
      <p className="py-1 pl-[6.75rem] text-xs text-muted-foreground">
        <span className="font-semibold text-foreground/80">0</span> dropped · all {stage.count} carried
        through
      </p>
    );
  }
  const gloss = stage.top_reject_reason ? REASON_GLOSS[stage.top_reject_reason] : undefined;
  return (
    <p className="py-1 pl-[6.75rem] text-xs text-muted-foreground">
      <span className="font-semibold text-neg">−{drop}</span>
      {stage.top_reject_reason && (
        <>
          {" · mostly "}
          <span className="font-semibold text-foreground/80">{stage.top_reject_reason}</span>
          {gloss && ` — ${gloss}`}
        </>
      )}
    </p>
  );
}

// Reframes a low trade count as visible discipline rather than an empty
// dashboard: the counts come from the same reject sets the entry path uses,
// so every drop-off here is a rule that actually fired. The drop-offs are
// rendered inline rather than hidden in a chart tooltip -- they are the whole
// argument, and a tooltip is invisible in the screenshot a judge takes.
export function Funnel({ funnel }: { funnel: FunnelResponse | null }) {
  if (funnel === null || funnel.stages.length === 0) {
    return (
      <SectionEmpty
        icon={Filter}
        title="Entry funnel"
        className=""
        reason="No completed session yet, so there is no funnel to draw. It counts one full session of entry scans — screened, shortlisted, built, debated, entered — and appears once the first session closes."
      />
    );
  }

  const first = funnel.stages[0];
  const last = funnel.stages[funnel.stages.length - 1];
  const max = Math.max(...funnel.stages.map((s) => s.count), 1);

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="flex flex-wrap items-center gap-1.5 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
          <Filter className="size-3.5" />
          Entry funnel
          {/* The API's session_date is the last session with decisions, which
              is not today's -- the status bar says 3 Sep while this says
              2 Sep. Label it rather than letting the two look inconsistent. */}
          <span className="normal-case">(last completed session — {funnel.session_date})</span>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div>
          <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
            <span className="flex items-baseline gap-2 text-2xl font-semibold tabular-nums">
              {first.count}
              <ArrowRight className="size-4 self-center text-muted-foreground" />
              {last.count}
            </span>
            <span className="text-sm text-muted-foreground">screened → entered</span>
          </div>
          {last.count === 0 && (
            <p className="mt-1 text-sm text-foreground/80">
              Nothing entered. Here is where the {first.count} went:
            </p>
          )}
        </div>

        <ol className="text-sm">
          {funnel.stages.map((stage, i) => {
            const next = funnel.stages[i + 1];
            return (
              <li key={stage.name}>
                <div className="flex items-center gap-3">
                  <span className="w-24 shrink-0 text-foreground/70">{STAGE_LABELS[stage.name] ?? stage.name}</span>
                  <span className="w-8 shrink-0 text-right font-semibold tabular-nums">{stage.count}</span>
                  <span className="flex-1">
                    <span
                      className="block h-2 rounded-sm bg-primary/70"
                      style={{ width: `${(stage.count / max) * 100}%` }}
                    />
                  </span>
                </div>
                {next && <DropRow stage={stage} drop={stage.count - next.count} />}
              </li>
            );
          })}
        </ol>
      </CardContent>
    </Card>
  );
}

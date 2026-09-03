import { Brain } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { SectionEmpty } from "@/components/SectionEmpty";
import { formatDateTime, verdictVariant } from "@/lib/format";
import type { Reflection as ReflectionShape } from "@/lib/types";

const UNAVAILABLE =
  "Reflection unavailable this session -- LLM budget or transport unavailable. Constraint histogram above is still real.";

function ConstraintLine({ reflection }: { reflection: ReflectionShape }) {
  return (
    <>
      <Badge variant={verdictVariant(reflection.verdict)}>{reflection.verdict}</Badge>
      <span className="text-sm tabular-nums text-foreground/70">
        <span className="font-semibold text-foreground">{reflection.binding_constraint}</span> ×{" "}
        {reflection.constraint_count} of {reflection.decisions_examined} decisions
      </span>
    </>
  );
}

// The agent's own post-market critique of the constraint that bound it that
// session -- degrades independently like every other section (page.tsx's
// established pattern: null in, null out). Heads the Decisions tab: the
// session's thesis, with the feed below it as the evidence.
export function Reflection({ reflection }: { reflection: ReflectionShape | null }) {
  if (reflection === null) {
    return (
      <SectionEmpty
        icon={Brain}
        title="Reflector"
        reason="No reflection yet. The Reflector runs once the market closes, on the session that just ended — it reads that session's decisions and trades and names the constraint that bound the agent."
      />
    );
  }

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="flex flex-wrap items-center gap-1.5 text-subheadline font-semibold uppercase tracking-wide text-muted-foreground">
          <Brain className="size-3.5" />
          Reflector
          <span className="normal-case">({reflection.session_date})</span>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <ConstraintLine reflection={reflection} />
        </div>
        <p className="text-sm text-foreground/70">{reflection.ok ? reflection.argument : UNAVAILABLE}</p>
        {reflection.proposed_change && (
          <p className="rounded-md border border-border/60 bg-muted/20 px-2 py-1 font-mono text-xs text-foreground/80">
            {reflection.proposed_change}
          </p>
        )}
        <p className="text-caption tabular-nums text-muted-foreground">{formatDateTime(reflection.ts_utc)}</p>
      </CardContent>
    </Card>
  );
}

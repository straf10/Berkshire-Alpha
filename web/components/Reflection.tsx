import { Brain } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { formatDateTime, verdictVariant } from "@/lib/format";
import type { Reflection as ReflectionShape } from "@/lib/types";

// The agent's own post-market critique of the constraint that bound it that
// session (docs/day4_action_plan.md Step 5) -- degrades independently like
// every other section (page.tsx's established pattern: null in, null out).
export function Reflection({ reflection }: { reflection: ReflectionShape | null }) {
  if (reflection === null) return null;

  return (
    <Card className="mb-6">
      <CardHeader className="pb-2">
        <CardTitle className="flex flex-wrap items-center gap-1.5 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
          <Brain className="size-3.5" />
          Reflector
          <span className="normal-case">({reflection.session_date})</span>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant={verdictVariant(reflection.verdict)}>{reflection.verdict}</Badge>
          <span className="text-sm text-foreground/70">
            <span className="font-semibold text-foreground">{reflection.binding_constraint}</span> ×{" "}
            {reflection.constraint_count} of {reflection.decisions_examined} decisions
          </span>
        </div>
        <p className="text-sm text-foreground/70">
          {reflection.ok ? reflection.argument : "Reflection unavailable this session -- LLM budget or transport unavailable. Constraint histogram above is still real."}
        </p>
        {reflection.proposed_change && (
          <p className="rounded-md border border-border/60 bg-muted/20 px-2 py-1 font-mono text-xs text-foreground/80">
            {reflection.proposed_change}
          </p>
        )}
        <p className="text-[11px] text-muted-foreground">{formatDateTime(reflection.ts_utc)}</p>
      </CardContent>
    </Card>
  );
}

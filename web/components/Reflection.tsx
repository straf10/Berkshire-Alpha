import { ArrowRight, Brain } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { formatDateTime, verdictVariant } from "@/lib/format";
import type { Reflection as ReflectionShape } from "@/lib/types";

const UNAVAILABLE =
  "Reflection unavailable this session -- LLM budget or transport unavailable. Constraint histogram above is still real.";

function ConstraintLine({ reflection }: { reflection: ReflectionShape }) {
  return (
    <>
      <Badge variant={verdictVariant(reflection.verdict)}>{reflection.verdict}</Badge>
      <span className="text-sm text-foreground/70">
        <span className="font-semibold text-foreground">{reflection.binding_constraint}</span> ×{" "}
        {reflection.constraint_count} of {reflection.decisions_examined} decisions
      </span>
    </>
  );
}

// The agent's own post-market critique of the constraint that bound it that
// session -- degrades independently like every other section (page.tsx's
// established pattern: null in, null out).
//
// Two placements, one component. `section` heads the Decisions tab: the
// session's thesis, with the feed below it as the evidence. `overview` is a
// quieter card on Overview that carries the argument itself, because a
// self-critique buried on the second tab may as well not exist -- it is one
// of the two things on this dashboard nothing else in the field does.
export function Reflection({
  reflection,
  variant = "section",
  onOpenDecisions,
}: {
  reflection: ReflectionShape | null;
  variant?: "section" | "overview";
  onOpenDecisions?: () => void;
}) {
  if (reflection === null) return null;

  if (variant === "overview") {
    return (
      /* Violet left rule: the Reflector is the one place on the page where a
         different voice is speaking -- the agent about itself, not the agent
         about the market. */
      <Card className="mb-6 border-l-2 border-l-accent">
        <CardHeader className="pb-2">
          <CardTitle className="flex flex-wrap items-center gap-1.5 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
            <Brain className="size-3.5" />
            Reflector
            <span className="normal-case">— the agent&apos;s own read on {reflection.session_date}</span>
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <blockquote className="border-l-2 border-accent/40 pl-3 text-base leading-relaxed text-foreground/90">
            {reflection.ok ? reflection.argument : UNAVAILABLE}
          </blockquote>
          <div className="flex flex-wrap items-center gap-2">
            <ConstraintLine reflection={reflection} />
            {onOpenDecisions && (
              <button
                type="button"
                onClick={onOpenDecisions}
                className="flex items-center gap-1 text-sm text-primary hover:underline"
              >
                Read the decisions
                <ArrowRight className="size-3.5" />
              </button>
            )}
          </div>
        </CardContent>
      </Card>
    );
  }

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
          <ConstraintLine reflection={reflection} />
        </div>
        <p className="text-sm text-foreground/70">{reflection.ok ? reflection.argument : UNAVAILABLE}</p>
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

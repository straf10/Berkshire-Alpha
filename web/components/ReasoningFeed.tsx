import { MessagesSquare } from "lucide-react";
import { DecisionCard } from "@/components/DecisionCard";
import type { Decision } from "@/lib/types";

// The centerpiece per PLAN.md: "our strongest asset for Presentation &
// Explainability". Each card lazy-fetches its full chain on first expand
// (DecisionCard), fixing the old page's eager N+1 verdict fetch.
export function ReasoningFeed({ decisions }: { decisions: Decision[] }) {
  return (
    <div className="mb-6">
      <p className="mb-2 flex items-center gap-1.5 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
        <MessagesSquare className="size-3.5" />
        Reasoning feed
      </p>
      {decisions.length === 0 ? (
        <p className="text-muted-foreground">No decisions yet.</p>
      ) : (
        <div className="space-y-2">
          {decisions.map((d) => (
            <DecisionCard key={d.id} decision={d} />
          ))}
        </div>
      )}
    </div>
  );
}

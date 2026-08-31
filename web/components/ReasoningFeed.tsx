import { DecisionCard } from "@/components/DecisionCard";
import type { Decision } from "@/lib/types";

// The centerpiece per PLAN.md: "our strongest asset for Presentation &
// Explainability". Each card lazy-fetches its full chain on first expand
// (DecisionCard), fixing the old page's eager N+1 verdict fetch.
export function ReasoningFeed({ decisions }: { decisions: Decision[] }) {
  if (decisions.length === 0) {
    return <p className="text-muted-foreground">No decisions yet.</p>;
  }

  return (
    <div className="space-y-2">
      {decisions.map((d) => (
        <DecisionCard key={d.id} decision={d} />
      ))}
    </div>
  );
}

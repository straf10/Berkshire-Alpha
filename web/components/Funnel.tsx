import { ArrowRight, Filter } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { FunnelBarChart } from "@/components/charts/FunnelBarChart";
import type { FunnelResponse } from "@/lib/types";

// Reframes a low trade count as visible discipline rather than an empty
// dashboard (docs/IMMEDIATE_IMPROVEMENT.md item 8 / day6_ui_plan.md S4 Funnel).
export function Funnel({ funnel }: { funnel: FunnelResponse | null }) {
  if (funnel === null) return null;

  const labels: Record<string, string> = {
    screened: "Screened",
    shortlisted: "Shortlisted",
    debated: "Debated",
    entered: "Entered",
  };

  const data = funnel.stages.map((s) => ({
    name: labels[s.name] ?? s.name,
    count: s.count,
    dropReason: s.top_reject_reason,
  }));

  return (
    <Card className="mb-6">
      <CardHeader className="pb-2">
        <CardTitle className="flex flex-wrap items-center gap-1.5 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
          <Filter className="size-3.5" />
          <span className="flex items-center gap-1.5">
            Screen <ArrowRight className="size-3" /> Shortlist <ArrowRight className="size-3" /> Debate{" "}
            <ArrowRight className="size-3" /> Gate
          </span>
          <span>({funnel.session_date})</span>
        </CardTitle>
      </CardHeader>
      <CardContent>
        <FunnelBarChart data={data} />
      </CardContent>
    </Card>
  );
}

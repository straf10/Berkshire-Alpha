import { ArrowRight, Filter } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { FunnelBarChart } from "@/components/charts/FunnelBarChart";
import type { FunnelResponse } from "@/lib/types";

// Reframes a low trade count as visible discipline rather than an empty
// dashboard: the counts come from the same reject sets the entry path uses,
// so every drop-off here is a rule that actually fired.
export function Funnel({ funnel }: { funnel: FunnelResponse | null }) {
  if (funnel === null) return null;

  const labels: Record<string, string> = {
    screened: "Screened",
    shortlisted: "Shortlisted",
    built: "Built",
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
            Screen <ArrowRight className="size-3" /> Shortlist <ArrowRight className="size-3" /> Build{" "}
            <ArrowRight className="size-3" /> Debate <ArrowRight className="size-3" /> Gate
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

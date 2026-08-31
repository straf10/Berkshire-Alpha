import { Coins } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { StatTile } from "@/components/StatTile";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatCost } from "@/lib/format";
import type { LlmUsageResponse } from "@/lib/types";

// One row per (node, model) pair -- node is the agent role (analyst/debate
// persona/trader/risk-manager) that made the call, so this is "which tool
// cost how much" per docs/day6_ui_plan.md-style monitoring, not just a
// single opaque total.
export function LlmUsage({ usage }: { usage: LlmUsageResponse | null }) {
  if (usage === null) return null;

  const { totals, by_node_model } = usage;
  if (totals.calls === 0) return null;

  return (
    <Card className="mb-6">
      <CardHeader className="pb-2">
        <CardTitle className="flex items-center gap-1.5 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
          <Coins className="size-3.5" />
          LLM usage &amp; cost
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className="mb-4 grid grid-cols-2 gap-4 sm:grid-cols-4">
          <StatTile label="Total cost" value={formatCost(totals.cost_usd)} />
          <StatTile label="API calls" value={totals.calls.toLocaleString()} />
          <StatTile label="Prompt tokens" value={totals.prompt_tokens.toLocaleString()} />
          <StatTile label="Completion tokens" value={totals.completion_tokens.toLocaleString()} />
        </div>
        <div className="overflow-x-auto rounded-md border border-border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Tool / node</TableHead>
                <TableHead>Model</TableHead>
                <TableHead>Calls</TableHead>
                <TableHead>Prompt tokens</TableHead>
                <TableHead>Completion tokens</TableHead>
                <TableHead>Cost</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {by_node_model.map((row) => (
                <TableRow key={`${row.node}-${row.model}`}>
                  <TableCell className="font-semibold">{row.node}</TableCell>
                  <TableCell className="text-foreground/70">{row.model}</TableCell>
                  <TableCell>{row.calls.toLocaleString()}</TableCell>
                  <TableCell>{row.prompt_tokens.toLocaleString()}</TableCell>
                  <TableCell>{row.completion_tokens.toLocaleString()}</TableCell>
                  <TableCell>{formatCost(row.cost_usd)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      </CardContent>
    </Card>
  );
}

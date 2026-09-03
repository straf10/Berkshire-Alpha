import { Coins } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { SectionEmpty } from "@/components/SectionEmpty";
import { StatTile } from "@/components/StatTile";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatCost } from "@/lib/format";
import type { LlmUsageResponse } from "@/lib/types";

// One row per (node, model) pair -- node is the agent role (analyst/debate
// persona/trader/risk-manager) that made the call, so this answers "which
// stage cost how much" rather than showing a single opaque total.
export function LlmUsage({
  usage,
  ordersSent,
  nodeModels,
}: {
  usage: LlmUsageResponse | null;
  /** Orders the agent actually sent, for the cost-per-order stat. */
  ordersSent: number;
  /** The live routing table, to date-stamp rows that predate it. */
  nodeModels?: Record<string, string>;
}) {
  if (usage === null || usage.totals.calls === 0) {
    return (
      <SectionEmpty
        icon={Coins}
        title="LLM usage & cost"
        reason="No LLM calls recorded yet on this deploy. Every model call is metered per node and per model as it happens — the first entry scan of the session fills this in."
      />
    );
  }

  const { totals, by_node_model } = usage;

  // One RISK_NEUTRAL call per risk-team run, so this counts candidates that
  // went the whole way -- analyst -> debate -> proposal -> risk vote -- rather
  // than every symbol the scanner looked at.
  const deliberated = by_node_model
    .filter((r) => r.node === "RISK_NEUTRAL")
    .reduce((sum, r) => sum + r.calls, 0);

  // Rows whose model is not the one this node is routed to today were made
  // before per-node routing landed. Computed, not asserted, so it stops
  // reporting a discrepancy the moment the next session runs.
  const preRouting = nodeModels
    ? by_node_model
        .filter((r) => nodeModels[r.node] && nodeModels[r.node] !== r.model)
        .reduce((sum, r) => sum + r.calls, 0)
    : 0;

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="flex items-center gap-1.5 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
          <Coins className="size-3.5" />
          LLM usage &amp; cost
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className="mb-2 grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-6">
          <StatTile label="Total cost" value={formatCost(totals.cost_usd)} />
          {ordersSent > 0 && (
            <StatTile label="Per order sent" value={formatCost(totals.cost_usd / ordersSent)} />
          )}
          {deliberated > 0 && (
            <StatTile label="Per deliberated candidate" value={formatCost(totals.cost_usd / deliberated)} />
          )}
          <StatTile label="API calls" value={totals.calls.toLocaleString()} />
          <StatTile label="Prompt tokens" value={totals.prompt_tokens.toLocaleString()} />
          <StatTile label="Completion tokens" value={totals.completion_tokens.toLocaleString()} />
        </div>
        <p className="mb-4 text-xs text-muted-foreground">
          Every LLM call this agent has ever made — {totals.calls.toLocaleString()} of them, across{" "}
          {deliberated > 0 ? `${deliberated} fully-deliberated candidates and ` : ""}
          {ordersSent} orders sent — cost {formatCost(totals.cost_usd)} in total.
        </p>
        <div className="rounded-md border border-border">
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
        {preRouting > 0 && (
          <p className="mt-2 text-[11px] text-muted-foreground">
            {preRouting.toLocaleString()} of {totals.calls.toLocaleString()} calls above ran on a
            model that node is no longer routed to — they predate per-node routing. The ensemble
            table is the routing in force now.
          </p>
        )}
      </CardContent>
    </Card>
  );
}

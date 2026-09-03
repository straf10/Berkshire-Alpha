import { Wrench } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { SectionEmpty } from "@/components/SectionEmpty";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import type { ToolUsageResponse } from "@/lib/types";

// Counts/latency/failure-rate per non-LLM tool (Alpaca market data, Alpaca
// CLI, News) -- these aren't metered per-call like LLM tokens, so this is
// the counts-and-reliability counterpart to LlmUsage's cost table.
export function ToolUsage({ usage }: { usage: ToolUsageResponse | null }) {
  if (usage === null || usage.totals.calls === 0) {
    return (
      <SectionEmpty
        icon={Wrench}
        title="Tool API calls"
        reason="No tool calls recorded yet on this deploy. Every non-LLM request the agent makes — Alpaca market data, the Alpaca CLI, the news feed — is counted here from the first scan onwards."
      />
    );
  }

  const { totals, by_tool_endpoint } = usage;
  const clean = totals.failures === 0;

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="flex items-center gap-1.5 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
          <Wrench className="size-3.5" />
          Tool API calls
        </CardTitle>
      </CardHeader>
      <CardContent>
        {/* The reliability record is the headline, not a footnote. It used to
            be two StatTiles in a four-column grid, which read as "some
            numbers about tools" -- the actual claim is that several hundred
            live broker and market-data calls went out with nothing failing,
            and that only lands if the two numbers are set next to each other
            in one sentence. */}
        <p className="text-2xl font-semibold tabular-nums">
          {totals.calls.toLocaleString()} calls
          <span className="px-2 font-normal text-muted-foreground">·</span>
          <span className={clean ? "text-pos" : "text-neg"}>
            {totals.failures.toLocaleString()} failure{totals.failures === 1 ? "" : "s"}
          </span>
        </p>
        <p className="mb-4 text-xs text-muted-foreground">
          Every non-LLM request the agent has made, across {by_tool_endpoint.length} endpoint
          {by_tool_endpoint.length === 1 ? "" : "s"}
          {clean ? " — none of them failed." : "."}
        </p>
        <div className="rounded-md border border-border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Tool</TableHead>
                <TableHead>Endpoint</TableHead>
                <TableHead>Calls</TableHead>
                <TableHead>Avg latency</TableHead>
                <TableHead>Failures</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {by_tool_endpoint.map((row) => (
                <TableRow key={`${row.tool}-${row.endpoint}`}>
                  <TableCell className="font-semibold">{row.tool}</TableCell>
                  <TableCell className="text-foreground/70">{row.endpoint}</TableCell>
                  <TableCell>{row.calls.toLocaleString()}</TableCell>
                  <TableCell>{Math.round(row.avg_latency_ms).toLocaleString()} ms</TableCell>
                  <TableCell>
                    {row.failures > 0 ? (
                      <Badge variant="destructive">{row.failures}</Badge>
                    ) : (
                      <span className="text-foreground/70">0</span>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      </CardContent>
    </Card>
  );
}

import { Wrench } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { StatTile } from "@/components/StatTile";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import type { ToolUsageResponse } from "@/lib/types";

// Counts/latency/failure-rate per non-LLM tool (Alpaca market data, Alpaca
// CLI, News, Reddit) -- these aren't metered per-call like LLM tokens, so
// this is the counts-and-reliability counterpart to LlmUsage's cost table.
export function ToolUsage({ usage }: { usage: ToolUsageResponse | null }) {
  if (usage === null) return null;

  const { totals, by_tool_endpoint } = usage;
  if (totals.calls === 0) return null;

  return (
    <Card className="mb-6">
      <CardHeader className="pb-2">
        <CardTitle className="flex items-center gap-1.5 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
          <Wrench className="size-3.5" />
          Tool API calls
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className="mb-4 grid grid-cols-2 gap-4 sm:grid-cols-4">
          <StatTile label="Total calls" value={totals.calls.toLocaleString()} />
          <StatTile label="Failures" value={totals.failures.toLocaleString()} />
        </div>
        <div className="overflow-x-auto rounded-md border border-border">
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

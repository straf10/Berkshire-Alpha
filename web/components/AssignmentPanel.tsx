import { TriangleAlert } from "lucide-react";
import { formatDateTime } from "@/lib/format";
import type { AssignmentEvent } from "@/lib/types";

export function AssignmentPanel({ events }: { events: AssignmentEvent[] }) {
  if (events.length === 0) return null;
  return (
    <div className="rounded-md border border-warn/30 bg-warn/10 p-3 text-base">
      <p className="mb-2 flex items-center gap-1.5 text-sm font-semibold uppercase tracking-wide text-warn">
        <TriangleAlert className="size-3.5" />
        Assignment reconciliation ({events.length})
      </p>
      <ul className="space-y-1">
        {events.map((e) => (
          <li key={e.id} className="text-foreground/70">
            {formatDateTime(e.ts_utc)} — {e.symbol} {e.reason} equity {e.equity_qty > 0 ? "+" : ""}
            {e.equity_qty} sh ({e.contracts} contract{e.contracts === 1 ? "" : "s"}) — equity{" "}
            {e.equity_status}, orphan {e.orphan_status}
          </li>
        ))}
      </ul>
    </div>
  );
}

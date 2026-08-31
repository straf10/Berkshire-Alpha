import type { AssignmentEvent } from "@/lib/types";

export function AssignmentPanel({ events }: { events: AssignmentEvent[] }) {
  if (events.length === 0) return null;
  return (
    <div className="mb-6 rounded-md border border-amber-500/30 bg-amber-500/10 p-3 text-base">
      <p className="mb-2 font-semibold text-amber-400">
        Assignment reconciliation ({events.length})
      </p>
      <ul className="space-y-1">
        {events.map((e) => (
          <li key={e.id} className="text-foreground/70">
            {e.ts_utc} — {e.symbol} {e.reason} equity {e.equity_qty > 0 ? "+" : ""}
            {e.equity_qty} sh ({e.contracts} contract{e.contracts === 1 ? "" : "s"}) — equity{" "}
            {e.equity_status}, orphan {e.orphan_status}
          </li>
        ))}
      </ul>
    </div>
  );
}

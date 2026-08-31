import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

// Shared wrapper for the "icon + uppercase label" header followed by a
// bordered, horizontally-scrollable table -- previously hand-rolled
// identically in OpenPositionsTable, TradeHistoryTable, and DecisionsLog.
export function DataTableSection({
  icon: Icon,
  title,
  isEmpty = false,
  emptyMessage = "Nothing here yet.",
  children,
}: {
  icon: LucideIcon;
  title: string;
  isEmpty?: boolean;
  emptyMessage?: string;
  children: ReactNode;
}) {
  return (
    <div className="mb-6">
      <p className="mb-2 flex items-center gap-1.5 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
        <Icon className="size-3.5" />
        {title}
      </p>
      {isEmpty ? (
        <p className="text-muted-foreground">{emptyMessage}</p>
      ) : (
        <div className="overflow-x-auto rounded-md border border-border">{children}</div>
      )}
    </div>
  );
}

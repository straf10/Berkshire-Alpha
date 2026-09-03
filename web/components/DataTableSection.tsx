import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

// Shared wrapper for the "icon + uppercase label" header followed by a
// bordered, horizontally-scrollable table -- previously hand-rolled
// identically in every table section as a bare div (no card
// background/ring), which visually mismatched the
// Card-based sections (LlmUsage, ToolUsage, AgentConfigPanel). Built on
// Card/CardHeader/CardTitle so every section -- table or stat card -- shares
// the same background/border treatment.
export function DataTableSection({
  icon: Icon,
  title,
  isEmpty = false,
  emptyMessage = "Nothing here yet.",
  aside,
  children,
}: {
  icon: LucideIcon;
  title: string;
  isEmpty?: boolean;
  emptyMessage?: string;
  // Rendered above the scroll container, inside the card -- for a legend or
  // note that belongs to the table but must not scroll horizontally with it.
  aside?: ReactNode;
  children: ReactNode;
}) {
  return (
    <Card className="mb-6">
      <CardHeader className="pb-2">
        <CardTitle className="flex items-center gap-1.5 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
          <Icon className="size-3.5" />
          {title}
        </CardTitle>
      </CardHeader>
      <CardContent>
        {isEmpty ? (
          <p className="text-muted-foreground">{emptyMessage}</p>
        ) : (
          <>
            {aside}
            <div className="overflow-x-auto rounded-md border border-border">{children}</div>
          </>
        )}
      </CardContent>
    </Card>
  );
}

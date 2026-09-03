import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { cn } from "@/lib/utils";

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
  footer,
  scrollHeight,
  children,
}: {
  icon: LucideIcon;
  title: string;
  isEmpty?: boolean;
  emptyMessage?: string;
  // Rendered above the scroll container, inside the card -- for a legend or
  // note that belongs to the table but must not scroll horizontally with it.
  aside?: ReactNode;
  // Rendered below the (optionally height-capped) table, inside the card and
  // outside its scroll region -- for pagination controls that must stay on
  // screen without scrolling along with the rows.
  footer?: ReactNode;
  // When set, caps the table region to this CSS height with its own internal
  // vertical scroll and a sticky header -- for a feed too long to let the
  // whole page scroll through.
  scrollHeight?: string;
  children: ReactNode;
}) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="flex items-center gap-1.5 text-subheadline font-semibold uppercase tracking-wide text-muted-foreground">
          <Icon className="size-3.5" />
          {title}
        </CardTitle>
      </CardHeader>
      <CardContent>
        {/* No overflow-x-auto on the wrapper below: ui/table.tsx already puts
            every table inside its own `relative w-full overflow-x-auto`
            container, so this was a second scroll region nested around the
            first. The inner one is the one that actually moves; this div only
            ever carried the border (and, with scrollHeight set, the vertical
            scroll and a sticky header -- scoped to this wrapper only, so
            other tables outside a height-capped DataTableSection are
            untouched). */}
        {isEmpty ? (
          <p className="text-muted-foreground">{emptyMessage}</p>
        ) : (
          <>
            {aside}
            <div
              className={cn(
                "rounded-md border border-border",
                scrollHeight && "overflow-y-auto [&_th]:sticky [&_th]:top-0 [&_th]:z-10 [&_th]:bg-card"
              )}
              style={scrollHeight ? { maxHeight: scrollHeight } : undefined}
            >
              {children}
            </div>
            {footer}
          </>
        )}
      </CardContent>
    </Card>
  );
}

import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

// Every optional endpoint on this page degrades independently, and every one
// of them used to degrade to `return null` -- so a judge on a cold Railway
// container or a slow connection got a page with holes in it and concluded
// the thing was broken.
//
// A missing section is not an error and should not read as one. It says what
// is missing and, more importantly, WHEN it arrives, because for almost all
// of them the answer is "on the next management tick" rather than "never".
//
// Same Card shell as DataTableSection so an empty section occupies the same
// slot, with the same header, as the section it stands in for.
export function SectionEmpty({
  icon: Icon,
  title,
  reason,
  className,
}: {
  icon: LucideIcon;
  /** The section's own heading -- unchanged from when it has data. */
  title: string;
  /** What is missing and what will produce it. */
  reason: ReactNode;
  className?: string;
}) {
  return (
    <Card className={className}>
      <CardHeader className="pb-2">
        <CardTitle className="flex items-center gap-1.5 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
          <Icon className="size-3.5" />
          {title}
        </CardTitle>
      </CardHeader>
      <CardContent>
        <p className="max-w-[70ch] text-sm text-muted-foreground">{reason}</p>
      </CardContent>
    </Card>
  );
}

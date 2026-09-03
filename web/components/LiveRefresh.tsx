"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

// Polls the server component's data every 60s via router.refresh() -- re-runs
// page.tsx's fetchJson calls and re-renders with fresh props, without a full
// page reload or losing client-side state (selected tab, expanded decision
// cards). Management ticks land every 5 minutes and entry scans twice a
// session, so a 1-minute poll already catches every state change with margin
// to spare -- a faster interval just adds load without surfacing anything sooner.
//
// Also renders the "last updated" footer clock, since it's the component
// that actually knows when the last poll fired -- ticks locally rather than
// depending on router.refresh()'s completion (App Router doesn't expose one
// cleanly from client code), so this reads as "last poll attempted", which
// for a healthy connection is the same thing.
export function LiveRefresh({ intervalMs = 60_000 }: { intervalMs?: number }) {
  const router = useRouter();
  const [lastUpdated, setLastUpdated] = useState<Date>(() => new Date());

  useEffect(() => {
    const id = setInterval(() => {
      router.refresh();
      setLastUpdated(new Date());
    }, intervalMs);
    return () => clearInterval(id);
  }, [router, intervalMs]);

  return (
    <span suppressHydrationWarning className="tabular-nums">
      {lastUpdated ? `last updated ${lastUpdated.toLocaleTimeString()}` : "—"}
    </span>
  );
}

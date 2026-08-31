"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

// Polls the server component's data every 5s via router.refresh() -- re-runs
// page.tsx's fetchJson calls and re-renders with fresh props, without a full
// page reload or losing client-side state (selected tab, expanded decision
// cards). PLAN.md's UI spec calls for "Polling (2-5s)", which this satisfies
// without a WebSocket -- this is a read-only dashboard with no action to
// react to instantly, so a poll is the right level of effort.
//
// Also renders the "last updated" footer clock, since it's the component
// that actually knows when the last poll fired -- ticks locally rather than
// depending on router.refresh()'s completion (App Router doesn't expose one
// cleanly from client code), so this reads as "last poll attempted", which
// for a healthy connection is the same thing.
export function LiveRefresh({ intervalMs = 5000 }: { intervalMs?: number }) {
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
    <span suppressHydrationWarning>
      {lastUpdated ? `last updated ${lastUpdated.toLocaleTimeString()}` : "—"}
    </span>
  );
}

"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

// Polls the server component's data every 5s via router.refresh() -- re-runs
// page.tsx's fetchJson calls and re-renders with fresh props, without a full
// page reload or losing client-side state (selected tab, expanded decision
// cards). Renders nothing; PLAN.md calls for "Polling (2-5s)", which this
// satisfies without a WebSocket -- this is a read-only dashboard with no
// action to react to instantly, so a poll is the right level of effort.
export function LiveRefresh({ intervalMs = 5000 }: { intervalMs?: number }) {
  const router = useRouter();

  useEffect(() => {
    const id = setInterval(() => router.refresh(), intervalMs);
    return () => clearInterval(id);
  }, [router, intervalMs]);

  return null;
}

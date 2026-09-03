// Shared between the server page (app/page.tsx, reading ?tab= from
// searchParams) and the client <Dashboard> (rendering the active tab) --
// kept out of Dashboard.tsx itself because a plain constant imported from a
// "use client" module resolves to an opaque client-reference stub when
// referenced from a Server Component, not the actual array.
export const VALID_TABS = ["overview", "decisions", "trades", "usage", "config"] as const;
export type TabId = (typeof VALID_TABS)[number];

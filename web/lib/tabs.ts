// Shared between the server page (app/page.tsx, reading ?tab= from
// searchParams) and the client <Dashboard> (rendering the active tab) --
// kept out of Dashboard.tsx itself because a plain constant imported from a
// "use client" module resolves to an opaque client-reference stub when
// referenced from a Server Component, not the actual array.
export const VALID_TABS = ["judges", "overview", "decisions", "trades", "usage", "config"] as const;
export type TabId = (typeof VALID_TABS)[number];

// The landing tab, and the one whose ?tab= is omitted from the URL. This is
// a submission surface before it is an operations console: a judge arriving
// cold gets the argument first, with one click through to the live telemetry
// that backs it. Flip this to "overview" to land on the live dashboard again
// -- app/page.tsx and Dashboard.tsx both read it, so one edit moves both.
export const DEFAULT_TAB: TabId = "judges";

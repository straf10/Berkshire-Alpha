// Shared between the server page (app/page.tsx, reading ?tab= from
// searchParams) and the client <Dashboard> (rendering the active tab) --
// kept out of Dashboard.tsx itself because a plain constant imported from a
// "use client" module resolves to an opaque client-reference stub when
// referenced from a Server Component, not the actual array.
export const VALID_TABS = ["judges", "overview", "decisions", "trades", "usage", "config"] as const;
export type TabId = (typeof VALID_TABS)[number];

// The landing tab, and the one whose ?tab= is omitted from the URL.
//
// A cold visitor lands on Overview: the live dashboard, doing its thing. The
// argument for the system is one click away and stays FIRST in the tab strip
// (the order is the JSX in Dashboard.tsx, not this array), so "For the Judges"
// is still the leftmost thing anyone reads -- it just is not what loads.
//
// This is deliberately independent of tab ORDER. Changing it moves only which
// tab opens and which one omits ?tab= from the URL; app/page.tsx and
// Dashboard.tsx both read it, so one edit moves both.
export const DEFAULT_TAB: TabId = "overview";

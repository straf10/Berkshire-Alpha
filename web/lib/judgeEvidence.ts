// Evidence for the "For the Judges" tab.
//
// THE RULE THIS FILE EXISTS TO ENFORCE: a number on the judges page is either
// (a) derived live, this render, from data the page already fetched, or
// (b) a constant that carries the file it was measured in and the moment it
//     was measured.
//
// There is no third category. Nothing on that tab is a number someone
// remembered, because the whole argument of the page is that we do not do
// that -- and a judge who greps one figure and finds it unsourced has been
// handed a reason to disbelieve the other thirty.
//
// Why constants at all, rather than computing everything from the API:
// GET /decisions is hard-capped at 200 rows server-side (agent/api/app.py:66,
// `min(limit, 200)`), and those 200 rows currently span only 2-3 Sep and are
// entirely quant-only. Every LLM debate, every entry, and every fill in the
// judged window sits BEHIND that window. Deriving the headline claims from
// the live feed would render "0 debates, 0 entries" and quietly understate
// the system. So: live where live is honest, stamped where it is not.

import type { AccountState, Decision, Trade } from "@/lib/types";

// --- Provenance ------------------------------------------------------------

export interface Sourced<T> {
  value: T;
  /** Repo-relative path, or a broker/API origin. Rendered, not decorative. */
  source: string;
  /** When this was measured. Rendered next to the value. */
  asof: string;
}

function sourced<T>(value: T, source: string, asof: string): Sourced<T> {
  return { value, source, asof };
}

export const JUDGED_ACCOUNT = {
  id: "bc8bc895-ec1e-4b9d-9f69-413432024e5e",
  number: "PA3UM9X4MN5X",
  startEquity: 100_000,
} as const;

export const REPO_URL = "https://github.com/straf10/Autonomous-Debate-Trading-Agent";

// --- Sealed-window measurements, each stamped ------------------------------

export const EVIDENCE = {
  /** docs/trial_ledger.md, fed to agent/backtest/dsr.py:22 N_TRIALS. */
  trials: sourced(16, "docs/trial_ledger.md", "2026-09-01"),

  /** docs/friction.md §2 -- regulatory cost of every contract-side we filled. */
  regulatoryCost: sourced(5.21, "docs/friction.md §2", "2026-09-03"),

  /** docs/friction.md §3 -- all 7 fills, entries AND exits, vs first submit. */
  slippageAllIn: sourced(1961, "docs/friction.md §3", "2026-09-03"),

  /** docs/friction.md §4 -- our ledger understating our own realised result. */
  ledgerDivergence: sourced(224, "docs/friction.md §4", "2026-09-03"),

  /**
   * Broker order history, 3 Sep. Reproduce with:
   *   alpaca order list --status closed --limit 500
   * then segment on a >0.05 jump in limit_price -- each walk restarts from a
   * fresh mid, so a jump is a new walk and everything between is one walk's
   * replaces. 255 orders on the day, in the 5 segments below.
   */
  unwindWalks: sourced(
    [
      { start: "13:30:02", end: "13:30:02", from: -2.03, to: -2.03, steps: 0, outcome: "canceled" },
      { start: "18:17:42", end: "18:17:42", from: -4.44, to: -4.44, steps: 0, outcome: "canceled" },
      { start: "18:41:47", end: "19:07:26", from: -5.01, to: -0.26, steps: 95, outcome: "canceled" },
      { start: "19:12:44", end: "19:43:15", from: -6.34, to: -0.69, steps: 113, outcome: "canceled" },
      { start: "19:48:33", end: "19:59:53", from: -4.94, to: -2.84, steps: 42, outcome: "expired" },
    ],
    "Alpaca order history (255 orders)",
    "2026-09-03T19:59:53Z"
  ),

  /** NBBO on both legs at the closing tick. The arbitrage violation itself. */
  invertedBook: sourced(
    [
      { occ: "LLY260904P01160000", label: "1160 put", bid: 5.58, ask: 10.11 },
      { occ: "LLY260904P01165000", label: "1165 put ($5 further ITM)", bid: 2.72, ask: 17.19 },
    ],
    "Alpaca indicative feed, latest-quotes",
    "2026-09-03T19:59:59Z"
  ),

  /** docs/onepager.md -- the pre-ac54d36 asymmetry that proves the defect. */
  walkAsymmetry: sourced(
    { debitSteps: [0, 1, 70, 95], creditSteps: [0, 0, 0, 0] },
    "docs/onepager.md",
    "2026-09-03"
  ),
} as const;

// --- Live derivations ------------------------------------------------------

export interface Slippage {
  filled: number;
  atMid: number;
  walked: number;
  /** Signed net, debit-positive, so (fill - submit) is adverse in both directions. */
  dollars: number;
  worst: Trade | null;
}

/**
 * Entry slippage, computed here from the same `trades` rows the Trades tab
 * renders. Deliberately NOT the $1,961 in docs/friction.md: that figure is
 * computed from broker order records and includes the three EXIT fills, which
 * have no row of their own in `trades` (an exit updates the entry's row). The
 * page shows both and says which is which -- a number that does not tie out
 * to its own stated source is worse than no number.
 */
export function entrySlippage(trades: Trade[] | null): Slippage {
  const filled = (trades ?? []).filter(
    (t) => t.status === "FILLED" && t.fill_price !== null
  );
  let dollars = 0;
  let atMid = 0;
  let worst: Trade | null = null;
  let worstAbs = 0;

  for (const t of filled) {
    // Signed convention (agent/execution/broker.py): debit positive, credit
    // negative. Paying more, or collecting less credit, both raise the net --
    // so one subtraction is adverse in both directions.
    const slip = (Number(t.fill_price) - Number(t.submitted_limit)) * t.qty * 100;
    dollars += slip;
    if (t.walk_steps === 0) atMid += 1;
    if (Math.abs(slip) > worstAbs) {
      worstAbs = Math.abs(slip);
      worst = t;
    }
  }

  return { filled: filled.length, atMid, walked: filled.length - atMid, dollars, worst };
}

export interface RefusalBreakdown {
  evaluated: number;
  entered: number;
  refused: number;
  sessions: string[];
  /** Descending by count. */
  reasons: { code: string; count: number }[];
}

/**
 * The refusal ledger over whatever window the API returned (200 rows, capped
 * server-side). The window is reported alongside the counts rather than
 * rounded off, because "200 evaluations" and "the whole competition" are
 * different claims and only one of them is true here.
 */
export function refusalBreakdown(decisions: Decision[]): RefusalBreakdown {
  const counts = new Map<string, number>();
  const sessions = new Set<string>();
  let entered = 0;

  for (const d of decisions) {
    sessions.add(d.session_date);
    if (d.action === "ENTER") entered += 1;
    else counts.set(d.gate_reason, (counts.get(d.gate_reason) ?? 0) + 1);
  }

  return {
    evaluated: decisions.length,
    entered,
    refused: decisions.length - entered,
    sessions: [...sessions].sort(),
    reasons: [...counts.entries()]
      .map(([code, count]) => ({ code, count }))
      .sort((a, b) => b.count - a.count),
  };
}

export interface WindowPnl {
  equity: number;
  pnl: number;
  pct: number;
}

/**
 * P&L against the $100,000 the account was opened with -- the competition's
 * own basis, not the previous close AccountVitals uses for its day number.
 */
export function windowPnl(account: AccountState | null): WindowPnl | null {
  const equity = account?.equity ? Number(account.equity) : NaN;
  if (!Number.isFinite(equity)) return null;
  const pnl = equity - JUDGED_ACCOUNT.startEquity;
  return { equity, pnl, pct: pnl / JUDGED_ACCOUNT.startEquity };
}

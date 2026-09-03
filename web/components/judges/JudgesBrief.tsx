"use client";

import {
  Activity,
  ArrowUpRight,
  Ban,
  Beaker,
  FlaskConical,
  Gauge,
  Landmark,
  Layers,
  ScrollText,
  ShieldCheck,
  Workflow,
} from "lucide-react";
import type { ReactNode } from "react";
import { WriteUp } from "@/components/judges/WriteUp";
import { Section, SectionHero } from "@/components/Section";
import { SystemFlow } from "@/components/SystemFlow";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { formatDateTime, formatMoney, formatPct, formatSignedMoney } from "@/lib/format";
import {
  EVIDENCE,
  JUDGED_ACCOUNT,
  REPO_URL,
  entrySlippage,
  refusalBreakdown,
  windowPnl,
  type Sourced,
} from "@/lib/judgeEvidence";
import { REASON_GLOSS } from "@/lib/rejectReasons";
import type {
  AccountState,
  AgentConfig,
  Decision,
  MarkGapResponse,
  Status,
  Trade,
} from "@/lib/types";

// ---------------------------------------------------------------------------
// Shared bits
// ---------------------------------------------------------------------------

/** The provenance line. Every stamped number on this tab wears one. */
function Provenance({ of }: { of: Sourced<unknown> }) {
  return (
    <p className="mt-1 text-caption-2 uppercase tracking-wide text-muted-foreground/70">
      {of.source} · {of.asof.replace("T", " ").replace("Z", " UTC")}
    </p>
  );
}

function Metric({
  label,
  value,
  tone = "neutral",
  note,
  provenance,
}: {
  label: string;
  value: ReactNode;
  tone?: "neutral" | "pos" | "neg" | "primary" | "warn";
  note?: ReactNode;
  provenance?: Sourced<unknown>;
}) {
  const TONE: Record<string, string> = {
    neutral: "text-foreground",
    pos: "text-pos",
    neg: "text-neg",
    primary: "text-primary",
    warn: "text-warn",
  };
  return (
    <div className="rounded-lg bg-surface-2/70 p-3">
      <p className="text-caption uppercase tracking-wide text-muted-foreground">{label}</p>
      <p className={`mt-0.5 text-title-2 font-semibold tabular-nums ${TONE[tone]}`}>{value}</p>
      {note && <p className="mt-1 text-xs leading-snug text-muted-foreground">{note}</p>}
      {provenance && <Provenance of={provenance} />}
    </div>
  );
}

/** A claim/counter-claim pair. The whole Criterion 3 argument is this shape. */
function Gap({
  n,
  icon: Icon,
  title,
  theirs,
  ours,
  children,
}: {
  n: number;
  icon: typeof Gauge;
  title: string;
  theirs: string;
  ours: string;
  children?: ReactNode;
}) {
  return (
    <Card>
      <CardContent className="flex flex-col gap-3">
        <div className="flex items-start gap-2.5">
          <span className="mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-md bg-primary/10 text-caption font-semibold tabular-nums text-primary">
            {n}
          </span>
          <p className="flex items-center gap-1.5 text-subheadline uppercase tracking-wide text-muted-foreground">
            <Icon className="size-3.5" />
            {title}
          </p>
        </div>

        <div className="grid gap-2 sm:grid-cols-2">
          <div className="rounded-lg border border-hairline bg-surface-2/50 p-3">
            <p className="text-caption uppercase tracking-wide text-muted-foreground/70">
              The usual claim
            </p>
            <p className="mt-1 text-sm leading-snug text-muted-foreground">{theirs}</p>
          </div>
          <div className="rounded-lg border border-primary/25 bg-primary/5 p-3">
            <p className="text-caption uppercase tracking-wide text-primary/80">What we measured</p>
            <p className="mt-1 text-sm leading-snug">{ours}</p>
          </div>
        </div>

        {children}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// The tab
// ---------------------------------------------------------------------------

export function JudgesBrief({
  status,
  account,
  config,
  decisions,
  trades,
  markgap,
  accountAsOf,
  onOpenDecisions,
}: {
  status: Status;
  account: AccountState | null;
  config: AgentConfig | null;
  decisions: Decision[];
  trades: Trade[] | null;
  markgap: MarkGapResponse | null;
  /**
   * When the agent last published the account snapshot (health.last_cycle_utc).
   * GET /state/account reads `agent_state`, which a trading cycle writes -- it
   * is NOT a broker read at page load, and this page does not get to imply it
   * is. Between cycles the equity here can differ from the broker's own by the
   * full move in an open position's mark.
   */
  accountAsOf: string | null;
  /** Criterion 4: hand the judge the raw reasoning, one click, same page. */
  onOpenDecisions: (gate?: string) => void;
}) {
  const pnl = windowPnl(account);
  const slip = entrySlippage(trades);
  const refusals = refusalBreakdown(decisions);
  const hasGaps = (markgap?.value.spreads.length ?? 0) > 0;
  const totalGap = hasGaps ? Number(markgap?.value.total_markgap ?? 0) : null;
  const sessionSpan =
    refusals.sessions.length > 1
      ? `${refusals.sessions[0]} → ${refusals.sessions[refusals.sessions.length - 1]}`
      : (refusals.sessions[0] ?? "—");

  return (
    <div className="flex flex-col gap-6">
      {/* ------------------------------------------------------------------ */}
      {/* Executive summary                                                   */}
      {/* ------------------------------------------------------------------ */}
      <Card>
        <CardContent className="flex flex-col gap-4">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="outline">Options Level 3</Badge>
            <Badge variant="outline">Paper · $100,000 start</Badge>
            <Badge variant="outline">MIT · open source</Badge>
            {status.entries_frozen && (
              <Badge variant="secondary">Entries frozen — final session</Badge>
            )}
          </div>

          <div>
            <p className="text-caption uppercase tracking-wide text-muted-foreground">
              Judged account {JUDGED_ACCOUNT.number}
            </p>
            <h2 className="mt-1 max-w-3xl text-title-2">
              An options agent whose models argue, and whose{" "}
              <em className="not-italic text-primary">risk layer cannot be argued with.</em>
            </h2>
          </div>

          <p className="max-w-3xl text-sm leading-relaxed text-muted-foreground">
            Volatility risk premium, harvested with defined-risk verticals on a 50-name universe
            ordered by <em className="not-italic">measured</em> 3–7 DTE chain liquidity. Regime
            selection is arithmetic — IV/RV, 25-delta skew, VWAP deviation, RSI, volume-weighted
            momentum. Only then do four models from three vendors run a Bull/Bear debate under a
            Disagree-or-Commit protocol. Then the part that matters:{" "}
            <strong className="font-semibold text-foreground">
              every model output lands in a deterministic Python gate that no prompt can reach.
            </strong>{" "}
            An adversarial test asserts that a fabricated <em className="not-italic">unanimous</em>{" "}
            LLM approval of an oversized trade is still rejected. A model-risk critic written as
            another prompt can be talked out of its veto. Ours cannot be prompted at all.
          </p>

          <div className="rounded-lg border border-warn/30 bg-warn/5 p-3">
            <p className="text-caption uppercase tracking-wide text-warn">
              What we would lead with in a post-mortem, so we lead with it here
            </p>
            <p className="mt-1 max-w-3xl text-sm leading-relaxed">
              Our primary strategy was mechanically disabled for the entire judged window, and
              nothing in the system could see it. Alpaca&apos;s SDK rejects{" "}
              <code className="rounded bg-muted px-1 text-xs">limit_price &lt;= 0</code> inside{" "}
              <code className="rounded bg-muted px-1 text-xs">ReplaceOrderRequest</code>&apos;s
              constructor — correct for a single leg, wrong for a spread, where a negative limit{" "}
              <em className="not-italic">is</em> the net credit. It raised before any HTTP call,
              escaped the adapter&apos;s{" "}
              <code className="rounded bg-muted px-1 text-xs">APIError</code> handler, and returned
              REJECTED without cancelling the order it had already placed — which then reserved the
              quantity at the broker and locked every later close.{" "}
              <strong className="font-semibold">
                That is why the P&amp;L below is negative. The cause is mechanical, not strategic,
                and we would rather submit that sentence than a clean dashboard.
              </strong>
            </p>
          </div>
        </CardContent>
      </Card>

      {/* ------------------------------------------------------------------ */}
      {/* Criterion 1 — P&L, framed as calibration                            */}
      {/* ------------------------------------------------------------------ */}
      <Section
        icon={Landmark}
        title="Criterion 1 · Performance"
        note="what four sessions can and cannot establish"
        meta={
          pnl ? `live · vs ${formatMoney(JUDGED_ACCOUNT.startEquity)} start` : "account feed down"
        }
      >
        <div className="flex flex-col gap-4">
          {pnl && (
            <div>
              <SectionHero
                value={formatSignedMoney(pnl.pnl)}
                suffix={formatPct(pnl.pct, 2)}
                tone={pnl.pnl >= 0 ? "pos" : "neg"}
              />
              <p className="mt-1 text-caption uppercase tracking-wide text-muted-foreground">
                Equity {formatMoney(pnl.equity)} · the agent&apos;s last published snapshot
                {accountAsOf ? `, ${formatDateTime(accountAsOf)}` : ""}
              </p>
            </div>
          )}

          <p className="max-w-3xl text-sm leading-relaxed text-muted-foreground">
            We are not going to dress this up.{" "}
            <strong className="font-semibold text-foreground">
              We are going to tell you what a number computed over four sessions is worth.
            </strong>{" "}
            An agent with a genuine 60% edge, run over twenty trades, still finishes behind a coin
            flip roughly three times in ten. At our sample size the estimator&apos;s own variance
            swamps the signal — which is why{" "}
            <code className="rounded bg-muted px-1 text-xs">agent/backtest/dsr.py</code> computes
            Deflated Sharpe Ratio and Minimum Track Record Length instead of a raw Sharpe. They are
            the only two statistics in the literature we follow that are built to be honest about a
            small, trial-contaminated sample. One week of P&amp;L is a single draw from a
            distribution. Calibration, fill realism and refusal discipline are the things that{" "}
            <em className="not-italic">are</em> observable in a week, so those are what we put in
            front of you.
          </p>

          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
            <Metric
              label="Trials counted for DSR"
              value={`N = ${EVIDENCE.trials.value}`}
              note="Every parameter revision — including one proposed, measured against data, and rejected."
              provenance={EVIDENCE.trials}
            />
            <Metric
              label="Fills at theoretical mid"
              value={`${slip.atMid} of ${slip.filled}`}
              tone="warn"
              note="Entry orders, zero walk steps, filled instantly at the mid of two separate NBBO quotes. Counting the three exits too it is 5 of 7 (docs/friction.md §3). No live options market behaves this way."
            />
            <Metric
              label="Entry slippage, measured"
              value={formatMoney(slip.dollars)}
              tone="neg"
              note="Computed live from this page's own trade rows. All-in including exits is $1,961 (docs/friction.md §3), which needs broker records an entry row cannot carry."
            />
            <Metric
              label="Regulatory cost, whole competition"
              value={formatMoney(EVIDENCE.regulatoryCost.value)}
              note="376× smaller than the slippage beside it. Fees are a rounding error; fill quality is the entire game."
              provenance={EVIDENCE.regulatoryCost}
            />
          </div>

          {/* Refusal framing */}
          <div className="rounded-lg border border-hairline bg-surface-2/50 p-4">
            <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
              <p className="flex items-center gap-1.5 text-subheadline uppercase tracking-wide text-muted-foreground">
                <Ban className="size-3.5" />
                The refusal ledger
              </p>
              <span className="text-caption tabular-nums text-muted-foreground">
                {refusals.evaluated} evaluations · {sessionSpan}
              </span>
            </div>

            <p className="mt-2 max-w-3xl text-sm leading-relaxed text-muted-foreground">
              Of the last{" "}
              <strong className="font-semibold tabular-nums text-foreground">
                {refusals.evaluated}
              </strong>{" "}
              symbol-evaluations,{" "}
              <strong className="font-semibold tabular-nums text-foreground">
                {refusals.refused}
              </strong>{" "}
              ended in a refusal — and{" "}
              <strong className="font-semibold text-foreground">
                every one names the deterministic rule that stopped it.
              </strong>{" "}
              This is not an idle agent. It is an agent that records why it declined, at symbol
              granularity, in a vocabulary that greps to a real constant in{" "}
              <code className="rounded bg-muted px-1 text-xs">agent/risk/gates.py</code>. An agent
              that cannot explain a refusal cannot be audited; an agent that never refuses is not
              managing risk, it is only placing orders.
            </p>

            <div className="mt-3 flex flex-col gap-1.5">
              {refusals.reasons.slice(0, 5).map((r) => (
                <button
                  key={r.code}
                  type="button"
                  onClick={() => onOpenDecisions(r.code)}
                  className="group flex w-full items-center gap-3 rounded-md px-2 py-1.5 text-left transition-colors hover:bg-muted/60"
                >
                  <span className="w-10 shrink-0 text-right text-sm font-semibold tabular-nums">
                    {r.count}
                  </span>
                  <span className="shrink-0 font-mono text-xs text-primary">{r.code}</span>
                  <span className="truncate text-xs text-muted-foreground">
                    {REASON_GLOSS[r.code] ?? "—"}
                  </span>
                  <ArrowUpRight className="ml-auto size-3.5 shrink-0 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100" />
                </button>
              ))}
            </div>
            <p className="mt-2 text-caption uppercase tracking-wide text-muted-foreground/70">
              Click any rule to open its decisions, filtered
            </p>
          </div>
        </div>
      </Section>

      {/* ------------------------------------------------------------------ */}
      {/* Criterion 2 — the pipeline                                          */}
      {/* ------------------------------------------------------------------ */}
      <Section
        icon={Workflow}
        title="Criterion 2 · Implementation"
        note="twelve stages, and which of them a model is allowed to touch"
        meta="the same live graph the cycle replay drives"
      >
        <div className="flex flex-col gap-3">
          <p className="max-w-3xl text-sm leading-relaxed text-muted-foreground">
            Read the lanes, not the boxes.{" "}
            <strong className="font-semibold text-foreground">Lane A is arithmetic</strong> —
            ingestion, chain validation against Alpaca&apos;s indicative greeks, regime selection.
            No model votes on any of it.{" "}
            <strong className="font-semibold text-foreground">Lane B is the debate</strong>, the
            only place an LLM holds an opinion; it can be short-circuited entirely and the agent
            keeps trading on the deterministic spine.{" "}
            <strong className="font-semibold text-foreground">Lane C is execution</strong> — the
            risk gate, the walked multi-leg order, assignment reconciliation, the Reflector. The
            gate sits between B and C on purpose: it is the narrowest point in the system, it is
            pure Python, and it is the last word.
          </p>
          <SystemFlow detail="full" />
        </div>
      </Section>

      {/* ------------------------------------------------------------------ */}
      {/* Criterion 3 — four gaps                                             */}
      {/* ------------------------------------------------------------------ */}
      <Section
        icon={FlaskConical}
        title="Criterion 3 · Originality"
        note="four things we found by trading, that a backtest cannot tell you"
      >
        <div className="flex flex-col gap-3">
          <p className="max-w-3xl text-sm leading-relaxed text-muted-foreground">
            Every item below is a measurement from the judged account, not a design intention. Each
            one changed the code.
          </p>

          <Gap
            n={1}
            icon={Gauge}
            title="Mid-price execution is a fiction"
            theirs="Backtests fill at the mid, so the strategy's P&L is the strategy's P&L."
            ours={`We filled ${slip.atMid} of ${slip.filled} entry orders at exactly the mid, on the first poll — 5 of 7 counting exits. The one order the venue did not simply hand us cost $1,884 on four contracts.`}
          >
            <div className="rounded-lg border border-hairline bg-surface-2/50 p-3">
              <p className="text-caption uppercase tracking-wide text-muted-foreground">
                LLY, trade 8 — a $5-wide vertical
              </p>
              <p className="mt-1 text-sm leading-snug">
                Walked <strong className="font-semibold tabular-nums">95 steps</strong> from a mid of{" "}
                <span className="font-mono tabular-nums">1.94</span> to a fill at{" "}
                <span className="font-mono tabular-nums text-neg">6.65</span> —{" "}
                <strong className="font-semibold">
                  133% of the structure&apos;s entire maximum value
                </strong>
                , an arbitrage-certain loss at the moment of execution. The walk cap was purely
                relative, so on a wide chain it floated with{" "}
                <code className="rounded bg-muted px-1 text-xs">natural</code> and never bit.
              </p>
              <p className="mt-2 text-sm leading-snug">
                Fixed by bounding a spread against the arbitrage limit its own strikes impose, in
                both directions:{" "}
                <code className="rounded bg-muted px-1 text-xs">
                  WALK_CAP_MAX_FRACTION_OF_WIDTH
                </code>
                {config && (
                  <span className="tabular-nums text-muted-foreground">
                    {" "}
                    = {config.execution_guardrails.walk_cap_max_fraction_of_width} opening,{" "}
                    {config.execution_guardrails.walk_cap_max_fraction_of_width_closing} closing
                  </span>
                )}
                . Those values are read live from{" "}
                <code className="rounded bg-muted px-1 text-xs">/config</code> — the running
                agent&apos;s, not a screenshot of them.
              </p>
            </div>

            <div className="rounded-lg border border-hairline bg-surface-2/50 p-3">
              <p className="text-caption uppercase tracking-wide text-muted-foreground">
                The other direction — and the reason for the negative result
              </p>
              <p className="mt-1 text-sm leading-snug">
                Debit walks stepped{" "}
                <span className="font-mono tabular-nums">
                  {EVIDENCE.walkAsymmetry.value.debitSteps.join(", ")}
                </span>
                . Credit walks stepped{" "}
                <span className="font-mono tabular-nums text-neg">
                  {EVIDENCE.walkAsymmetry.value.creditSteps.join(", ")}
                </span>
                . Four closing walks across three sessions, not one replace between them. That
                asymmetry <em className="not-italic">is</em> the signature of the SDK defect above,
                and it is only visible because we log every step of every walk.
              </p>
              <Provenance of={EVIDENCE.walkAsymmetry} />
            </div>
          </Gap>

          <Gap
            n={2}
            icon={Activity}
            title="Pin and assignment risk, under live conditions"
            theirs="Positions close at the mid on the exit signal; assignment is a footnote."
            ours="Our final unwind ran five walks and 255 orders into an arbitrage-violating book, conceded to $0.26 on a spread carrying $4.38 of intrinsic value, and still did not fill."
          >
            <div className="rounded-lg border border-neg/25 bg-neg/5 p-3">
              <p className="text-caption uppercase tracking-wide text-neg">
                The 3 Sep unwind, from the broker&apos;s own order history
              </p>
              <div className="mt-2 overflow-x-auto">
                <table className="w-full min-w-[420px] text-xs tabular-nums">
                  <thead className="text-muted-foreground">
                    <tr className="text-left">
                      <th className="pb-1 pr-3 font-medium">Window</th>
                      <th className="pb-1 pr-3 font-medium">Limit walked</th>
                      <th className="pb-1 pr-3 text-right font-medium">Steps</th>
                      <th className="pb-1 font-medium">Outcome</th>
                    </tr>
                  </thead>
                  <tbody className="font-mono">
                    {EVIDENCE.unwindWalks.value.map((w) => (
                      <tr key={w.start} className="border-t border-hairline">
                        <td className="py-1 pr-3 whitespace-nowrap">
                          {w.start}
                          {w.end !== w.start && ` → ${w.end}`}
                        </td>
                        <td className="py-1 pr-3 whitespace-nowrap">
                          {w.from.toFixed(2)}
                          {w.to !== w.from && ` → ${w.to.toFixed(2)}`}
                        </td>
                        <td className="py-1 pr-3 text-right">{w.steps}</td>
                        <td
                          className={
                            w.outcome === "expired"
                              ? "py-1 text-neg"
                              : "py-1 text-muted-foreground"
                          }
                        >
                          {w.outcome}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="mt-2 text-sm leading-snug">
                The walker is not at fault here — it stepped correctly, in the right direction, all
                the way down to a giveaway price.{" "}
                <strong className="font-semibold">The book was inverted.</strong> At the closing tick
                the 1165 put, five dollars further in the money, bid{" "}
                <span className="font-mono tabular-nums">
                  {EVIDENCE.invertedBook.value[1].bid.toFixed(2)}
                </span>{" "}
                against the 1160 put&apos;s{" "}
                <span className="font-mono tabular-nums">
                  {EVIDENCE.invertedBook.value[0].bid.toFixed(2)}
                </span>
                . A vertical whose long leg bids below its short leg is not a wide market; it is an
                arbitrage-violating one, and no net-credit limit can cross it.
              </p>
              <Provenance of={EVIDENCE.invertedBook} />
            </div>

            <p className="text-sm leading-relaxed text-muted-foreground">
              What we built for this: assignment reconciliation on a{" "}
              <strong className="font-semibold tabular-nums text-foreground">
                {config ? config.scan_schedule.management_interval_s / 60 : 5}-minute
              </strong>{" "}
              management tick that detects an assignment, flattens the resulting equity leg and
              closes the orphaned option — never LLM-invoked, and the only place this agent may send
              an equity order at all. Plus a leg-by-leg close fallback for a structural multi-leg
              rejection: short leg first, abort untouched if it does not fully fill, long leg after,{" "}
              <strong className="font-semibold text-foreground">never concurrent</strong>, because
              the one state worse than a stuck spread is half of one.
            </p>
          </Gap>

          <Gap
            n={3}
            icon={Layers}
            title="Greeks are fragile, so we refuse to invent them"
            theirs="Black-Scholes the greeks on the fly from a spot price and a guessed vol."
            ours="Alpaca's default OPRA feed returns all-zero greeks and null IV on this account. We verified that on day one and route every greek through the indicative feed instead."
          >
            <p className="text-sm leading-relaxed text-muted-foreground">
              A contract whose greeks block is degenerate — null IV, an all-zero vector, a
              non-positive or inverted quote — is{" "}
              <strong className="font-semibold text-foreground">dropped</strong>, not modelled
              around. The live LLY loss then showed the filter was still incomplete: a market of{" "}
              <span className="font-mono tabular-nums">8.90 / 15.09</span>, 51.6% wide, passed every
              gate we had, because no bid-ask width check existed anywhere in the pipeline. It does
              now
              {config && (
                <span className="tabular-nums">
                  {" "}
                  (<code className="rounded bg-muted px-1 text-xs">MAX_QUOTE_SPREAD_PCT</code> ={" "}
                  {config.execution_guardrails.max_quote_spread_pct})
                </span>
              )}
              , applied at chain intake only — deliberately never to a position we already hold,
              because a filter that hides an open position from the exit path is worse than the wide
              quote it was built to catch.
            </p>
            {totalGap !== null && (
              <div className="rounded-lg border border-warn/30 bg-warn/5 p-3">
                <p className="text-caption uppercase tracking-wide text-warn">
                  And we measure it in the other direction too
                </p>
                <p className="mt-1 text-sm leading-snug">
                  <code className="rounded bg-muted px-1 text-xs">/markgap</code> checks the
                  broker&apos;s own mark against the arbitrage band the strikes impose. It currently
                  reports{" "}
                  <strong className="font-semibold tabular-nums text-warn">
                    {formatMoney(totalGap)}
                  </strong>{" "}
                  of mark sitting outside that band — a number inside the equity this competition is
                  scored on. We publish it rather than net it out.
                </p>
              </div>
            )}
          </Gap>

          <Gap
            n={4}
            icon={Beaker}
            title="A trial you did not count is a trial you cannot deflate"
            theirs="Sweep the parameters, publish the best Sharpe."
            ours={`Parameters were frozen before the judged sessions, and all ${EVIDENCE.trials.value} revisions that produced them are logged with the commit and the measurement that justified each one.`}
          >
            <p className="text-sm leading-relaxed text-muted-foreground">
              <code className="rounded bg-muted px-1 text-xs">docs/preregistration.md</code> seals
              the window.{" "}
              <code className="rounded bg-muted px-1 text-xs">docs/trial_ledger.md</code> is the
              ledger, and its{" "}
              <strong className="font-semibold tabular-nums text-foreground">
                N = {EVIDENCE.trials.value}
              </strong>{" "}
              feeds <code className="rounded bg-muted px-1 text-xs">dsr.py</code> directly. It
              includes a row where an alternative was proposed, measured, and{" "}
              <em className="not-italic">rejected</em> — the shipped value never changed, and it is
              still a trial, because pretending otherwise is exactly the multiple-comparisons cheat
              DSR exists to correct. The post-freeze parameter sweep is excluded and says so in its
              own text: it selects no live value.
            </p>
            <p className="text-sm leading-relaxed text-muted-foreground">
              We audit our own harness in public too.{" "}
              <code className="rounded bg-muted px-1 text-xs">docs/report.md</code> records the{" "}
              <strong className="font-semibold text-foreground">VRP tautology</strong> — our
              synthetic chain derived IV from RV, so the backtest could never once enter a debit
              trade. And <code className="rounded bg-muted px-1 text-xs">docs/friction.md</code> §4
              records a{" "}
              <strong className="font-semibold tabular-nums text-foreground">
                {formatMoney(EVIDENCE.ledgerDivergence.value)}
              </strong>{" "}
              divergence between our ledger and the broker&apos;s that we found while writing it and
              published rather than backfilled.
            </p>
          </Gap>
        </div>
      </Section>

      {/* ------------------------------------------------------------------ */}
      {/* Criterion 4 — go look                                               */}
      {/* ------------------------------------------------------------------ */}
      <Section
        icon={ShieldCheck}
        title="Criterion 4 · Verify it yourself"
        note="nothing on this page is a screenshot"
      >
        <div className="grid gap-2 sm:grid-cols-3">
          <button
            type="button"
            onClick={() => onOpenDecisions()}
            className="flex flex-col gap-1 rounded-lg border border-primary/25 bg-primary/5 p-3 text-left transition-colors hover:bg-primary/10"
          >
            <span className="flex items-center gap-1.5 text-subheadline text-primary">
              Raw decision telemetry
              <ArrowUpRight className="size-3.5" />
            </span>
            <span className="text-xs leading-snug text-muted-foreground">
              Every debate turn, risk vote, proposal and gate verdict at symbol granularity —
              expandable, deep-linkable, unedited.
            </span>
          </button>
          <a
            href={REPO_URL}
            target="_blank"
            rel="noreferrer"
            className="flex flex-col gap-1 rounded-lg border border-hairline bg-surface-2/50 p-3 transition-colors hover:bg-muted/60"
          >
            <span className="flex items-center gap-1.5 text-subheadline">
              The repository
              <ArrowUpRight className="size-3.5" />
            </span>
            <span className="text-xs leading-snug text-muted-foreground">
              MIT. Every reject string on this page greps to a real constant. The read-only API
              cannot place an order — enforced by an import-graph test.
            </span>
          </a>
          <div className="flex flex-col gap-1 rounded-lg border border-hairline bg-surface-2/50 p-3">
            <span className="text-subheadline">The account</span>
            <span className="font-mono text-xs break-all text-muted-foreground">
              {JUDGED_ACCOUNT.number}
              <br />
              {JUDGED_ACCOUNT.id}
            </span>
          </div>
        </div>
      </Section>

      {/* ------------------------------------------------------------------ */}
      {/* Appendix                                                            */}
      {/* ------------------------------------------------------------------ */}
      <Section icon={ScrollText} title="Appendix · The one-page write-up" note="in full, no link out">
        <WriteUp />
      </Section>
    </div>
  );
}

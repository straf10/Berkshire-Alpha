"use client";

import {
  ArrowLeftRight,
  Coins,
  Database,
  Gavel,
  GitCommitHorizontal,
  Globe,
  LayoutDashboard,
  MessagesSquare,
  Server,
  Settings,
} from "lucide-react";
import Image from "next/image";
import { useState } from "react";
import { AccountVitals } from "@/components/AccountVitals";
import { AgentConfigPanel } from "@/components/AgentConfigPanel";
import { AssignmentPanel } from "@/components/AssignmentPanel";
import { CycleTheatre } from "@/components/CycleTheatre";
import { FeaturedWalk } from "@/components/FeaturedWalk";
import { GreeksGauges } from "@/components/GreeksGauges";
import { HealthStrip } from "@/components/HealthStrip";
import { JudgesBrief } from "@/components/judges/JudgesBrief";
import { LiveRefresh } from "@/components/LiveRefresh";
import { LlmUsage } from "@/components/LlmUsage";
import { ModelEnsemble } from "@/components/ModelEnsemble";
import { OpenPositionsTable } from "@/components/OpenPositionsTable";
import { ReasoningFeed } from "@/components/ReasoningFeed";
import { Reflection } from "@/components/Reflection";
import { StatusBar } from "@/components/StatusBar";
import { MarkGapPanel } from "@/components/MarkGapPanel";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ToolUsage } from "@/components/ToolUsage";
import { TradeHistoryTable } from "@/components/TradeHistoryTable";
import { formatDateTime } from "@/lib/format";
import { DEFAULT_TAB, VALID_TABS, type TabId } from "@/lib/tabs";
import type {
  AccountState,
  AgentConfig,
  AssignmentEvent,
  Decision,
  EquityPoint,
  GreeksSnapshot,
  HealthBucket,
  HealthResponse,
  LlmUsageResponse,
  MarkGapResponse,
  OpenPosition,
  Reflection as ReflectionShape,
  Status,
  ToolUsageResponse,
  Trade,
} from "@/lib/types";

const REPO_URL = "https://github.com/straf10/Autonomous-Debate-Trading-Agent";

// The commit this bundle was built from, baked in by next.config.ts's `env`
// block. Deploys go out through CI to Vercel, which nobody here can inspect
// directly -- rendering the sha makes "did my commit actually ship?" answerable
// from the page itself. Links to the commit unless the build had no git
// context at all, in which case gitSha() yields "unknown" and there is nothing
// to link to.
function BuildSha() {
  const sha = process.env.BUILD_SHA ?? "unknown";
  const body = (
    <>
      <GitCommitHorizontal className="size-3.5" />
      <span className="text-caption-2 uppercase tracking-wide text-muted-foreground/70">Build</span>
      {sha}
    </>
  );
  const className = "flex items-center gap-1.5";
  const title = "Git commit this dashboard was built from";

  if (sha === "unknown") {
    return (
      <span className={className} title={title}>
        {body}
      </span>
    );
  }
  return (
    <a
      className={`${className} hover:text-foreground`}
      title={title}
      href={`${REPO_URL}/commit/${sha}`}
      target="_blank"
      rel="noreferrer"
    >
      {body}
    </a>
  );
}

function Footer({
  backendLastUpdated,
  dbLastUpdated,
  frontendLastUpdated,
}: {
  backendLastUpdated: string | null;
  dbLastUpdated: string | null;
  frontendLastUpdated: string;
}) {
  return (
    <footer className="mt-8 flex flex-col items-center gap-3 border-t border-border/60 pt-3 text-center text-sm text-muted-foreground">
      <span>
        Founders{" "}
        <a href="https://github.com/straf10" target="_blank" rel="noreferrer" className="hover:text-foreground" title="GitHub: straf10">
          @straf10
        </a>{" "}
        &amp;{" "}
        <a href="https://github.com/stanimeros" target="_blank" rel="noreferrer" className="hover:text-foreground" title="GitHub: stanimeros">
          @stanimeros
        </a>
      </span>
      <div className="flex flex-wrap items-center justify-center gap-x-6 gap-y-1 border-t border-border/40 pt-3 text-xs tabular-nums">
        <span className="flex items-center gap-1.5" title="Last time this page fetched fresh data from the API">
          <Globe className="size-3.5" />
          <span className="text-caption-2 uppercase tracking-wide text-muted-foreground/70">UI</span>
          {formatDateTime(frontendLastUpdated)}
        </span>
        <span className="flex items-center gap-1.5" title="Last completed backend trading-loop cycle">
          <Server className="size-3.5" />
          <span className="text-caption-2 uppercase tracking-wide text-muted-foreground/70">Agent</span>
          {backendLastUpdated ? formatDateTime(backendLastUpdated) : "—"}
        </span>
        <span className="flex items-center gap-1.5" title="Timestamp of the most recent decision written to the database">
          <Database className="size-3.5" />
          <span className="text-caption-2 uppercase tracking-wide text-muted-foreground/70">Data</span>
          {dbLastUpdated ? formatDateTime(dbLastUpdated) : "—"}
        </span>
        <BuildSha />
      </div>
    </footer>
  );
}

export function Dashboard({
  initialTab,
  initialDecisionId,
  initialGates,
  status,
  decisions,
  assignments,
  config,
  account,
  equityHistory,
  greeksLatest,
  openPositions,
  trades,
  llmUsage,
  toolUsage,
  healthHistory,
  health,
  reflection,
  markgap,
  frontendLastUpdated,
}: {
  initialTab: TabId;
  initialDecisionId: number | null;
  initialGates: string[];
  status: Status;
  decisions: Decision[];
  assignments: AssignmentEvent[];
  config: AgentConfig | null;
  account: AccountState | null;
  equityHistory: EquityPoint[] | null;
  greeksLatest: GreeksSnapshot | null;
  openPositions: OpenPosition[] | null;
  trades: Trade[] | null;
  llmUsage: LlmUsageResponse | null;
  toolUsage: ToolUsageResponse | null;
  healthHistory: HealthBucket[] | null;
  health: HealthResponse | null;
  reflection: ReflectionShape | null;
  markgap: MarkGapResponse | null;
  frontendLastUpdated: string;
}) {
  const [tab, setTab] = useState<TabId>(initialTab);
  // Controlled here, not just handed to ReasoningFeed once, because the
  // judges tab drives it: clicking a rule in the refusal ledger has to land
  // on Decisions already filtered to that rule.
  const [gates, setGates] = useState<string[]>(initialGates);
  const walkCap = config ? Number(config.execution_guardrails.walk_cap_fraction) : null;

  // Tab switches never navigate -- everything was already fetched once on
  // load. Only the URL's ?tab= is updated (via the History API directly,
  // bypassing the Next.js router) so a reload or shared link restores the
  // same tab without re-running the page's data fetch.
  function handleTabChange(value: unknown) {
    const next = (VALID_TABS as readonly string[]).includes(value as string) ? (value as TabId) : DEFAULT_TAB;
    setTab(next);
    // Only the tab param is rewritten -- ?decision= and ?gate= are the deep
    // link and have to survive a tab switch, which this used to discard by
    // rebuilding the whole query string from scratch.
    const url = new URL(window.location.href);
    if (next === DEFAULT_TAB) url.searchParams.delete("tab");
    else url.searchParams.set("tab", next);
    window.history.replaceState(null, "", `${url.pathname}${url.search}`);
  }

  // Criterion 4's whole promise is "go look at the raw thing", so the CTA has
  // to actually land there -- switching the tab AND the filter, and writing
  // both into the URL so the judge can share exactly what they were shown.
  function openDecisions(gate?: string) {
    const next = gate ? [gate] : [];
    setGates(next);
    setTab("decisions");
    const url = new URL(window.location.href);
    url.searchParams.set("tab", "decisions");
    if (gate) url.searchParams.set("gate", gate);
    else url.searchParams.delete("gate");
    window.history.replaceState(null, "", `${url.pathname}${url.search}`);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  return (
    <main className="mx-auto w-full max-w-7xl p-4 text-base sm:p-8">
      {/* Layout rule: prose and chrome read at max-w-5xl, data gets the full
          max-w-7xl `main` allows -- applied per section, not per region, so a
          wide table can breathe while a paragraph never runs to 1280px. The
          header and footer are chrome; the Reflector's argument is prose;
          account, greeks, and tables are data. */}
      <div className="mx-auto w-full max-w-5xl">
        <div className="mb-1 flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-3">
            <Image src="/logo-earthy.png" alt="" width={64} height={64} className="rounded-lg" />
            <h1 className="text-title-2">Berkshire Alpha</h1>
          </div>
          <span className="text-caption tabular-nums text-muted-foreground">
            <LiveRefresh />
          </span>
        </div>
      </div>

      {/* Sticky band so the halt/scan state and the countdown stay on screen
          while a judge scrolls a long tab. Same glass language as every
          card, just anchored to the viewport instead of the page, and a
          shade more opaque so scrolling content never fights the text
          sitting on top of it. */}
      <div className="glass-bar sticky top-0 z-10 -mx-4 px-4 pb-3 pt-2 sm:-mx-8 sm:px-8">
        <div className="mx-auto w-full max-w-5xl">
          <StatusBar status={status} />
        </div>
      </div>

      <div className="mx-auto mt-4 w-full max-w-5xl">
        {/* Alert-like and reference material stay outside the tabs -- an
            assignment event matters regardless of which tab a judge is on. */}
        <AssignmentPanel events={assignments} />
      </div>

      <Tabs value={tab} onValueChange={handleTabChange} className="mt-6">
        <TabsList variant="line" className="mb-6 w-full">
          <TabsTrigger value="judges" className="gap-1.5">
            <Gavel className="size-3.5" />
            For the Judges
          </TabsTrigger>
          <TabsTrigger value="overview" className="gap-1.5">
            <LayoutDashboard className="size-3.5" />
            Overview
          </TabsTrigger>
          <TabsTrigger value="decisions" className="gap-1.5">
            <MessagesSquare className="size-3.5" />
            Decisions
          </TabsTrigger>
          <TabsTrigger value="trades" className="gap-1.5">
            <ArrowLeftRight className="size-3.5" />
            Trades
          </TabsTrigger>
          <TabsTrigger value="usage" className="gap-1.5">
            <Coins className="size-3.5" />
            Usage
          </TabsTrigger>
          <TabsTrigger value="config" className="gap-1.5">
            <Settings className="size-3.5" />
            Config
          </TabsTrigger>
        </TabsList>

        {/* The submission argument, and the tab a cold visitor lands on
            (lib/tabs.ts DEFAULT_TAB). Reads only from props already fetched
            above -- it issues no request of its own. */}
        <TabsContent value="judges">
          <JudgesBrief
            status={status}
            account={account}
            config={config}
            decisions={decisions}
            trades={trades}
            markgap={markgap}
            accountAsOf={health?.last_cycle_utc ?? null}
            onOpenDecisions={openDecisions}
          />
        </TabsContent>

        {/* Overview: glanceable totals only -- account state, risk gauges,
            and system health. No raw rows here; drill-down content
            (decisions, trades) lives in its own tab. */}
        <TabsContent value="overview" className="flex flex-col gap-4">
          <AccountVitals account={account} history={equityHistory} sessionDate={status.session_date} />
          {/* The architecture argument is the second thing a judge sees, not
              something they have to find on a tab called "How it works" -- and
              it earns the position by DOING something rather than documenting:
              it replays the last full cycle through itself. */}
          <CycleTheatre
            decisions={decisions}
            trades={trades}
            status={status}
            health={health}
            walkCapFraction={walkCap}
          />
          <GreeksGauges snapshot={greeksLatest} />
          <HealthStrip buckets={healthHistory} status={status} />
        </TabsContent>

        {/* Decisions: the reasoning feed is the whole tab. It previously sat
            under a "Decisions log" table that rendered the same seven columns
            from the same array with none of the expand behaviour -- a strict
            subset, so it is gone. */}
        <TabsContent value="decisions" className="flex flex-col gap-4">
          {/* The Reflector leads: it is the session's thesis, and the feed
              below it is the evidence. It used to be the last card under a
              fifty-row table. */}
          <Reflection reflection={reflection} />
          {/* Keyed on the filter: ReasoningFeed seeds its facet selection in a
              useState initialiser, so a prop change alone would not re-filter.
              Remounting is the honest fix at this size -- the alternative is
              lifting its whole facet state up for one caller. */}
          <ReasoningFeed
            key={gates.join(",")}
            decisions={decisions}
            walkCapFraction={walkCap}
            initialDecisionId={initialDecisionId}
            initialGates={gates}
          />
        </TabsContent>

        <TabsContent value="trades" className="flex flex-col gap-4">
          {/* The best single proof of execution engineering was three clicks
              deep inside an expanded table row. */}
          <FeaturedWalk trades={trades} walkCapFraction={walkCap} />
          <OpenPositionsTable positions={openPositions} assignments={assignments} />
          <MarkGapPanel markgap={markgap} />
          <TradeHistoryTable trades={trades} />
        </TabsContent>

        {/* Usage: cost and reliability only -- "is the agent healthy" lives in
            Overview's HealthStrip, not here. Cost leads: what every call
            actually cost, then the routing table that explains why. */}
        <TabsContent value="usage" className="flex flex-col gap-4">
          <LlmUsage
            usage={llmUsage}
            ordersSent={trades?.length ?? 0}
            nodeModels={config?.llm.node_models}
          />
          <ModelEnsemble config={config} />
          <ToolUsage usage={toolUsage} />
        </TabsContent>

        <TabsContent value="config" className="flex flex-col gap-4">
          <AgentConfigPanel config={config} />
        </TabsContent>
      </Tabs>

      <div className="mx-auto w-full max-w-5xl">
        <Footer
          backendLastUpdated={health?.last_cycle_utc ?? null}
          dbLastUpdated={decisions[0]?.ts_utc ?? null}
          frontendLastUpdated={frontendLastUpdated}
        />
      </div>
    </main>
  );
}

"use client";

import {
  ArrowLeftRight,
  Coins,
  Database,
  GitCommitHorizontal,
  Globe,
  LayoutDashboard,
  MessagesSquare,
  Server,
  Settings,
  Workflow,
} from "lucide-react";
import Image from "next/image";
import { useState } from "react";
import { AccountVitals } from "@/components/AccountVitals";
import { AgentConfigPanel } from "@/components/AgentConfigPanel";
import { AssignmentPanel } from "@/components/AssignmentPanel";
import { CycleTheatre } from "@/components/CycleTheatre";
import { FeaturedWalk } from "@/components/FeaturedWalk";
import { Funnel } from "@/components/Funnel";
import { GreeksGauges } from "@/components/GreeksGauges";
import { HealthStrip } from "@/components/HealthStrip";
import { LiveRefresh } from "@/components/LiveRefresh";
import { LlmUsage } from "@/components/LlmUsage";
import { ModelEnsemble } from "@/components/ModelEnsemble";
import { OpenPositionsTable } from "@/components/OpenPositionsTable";
import { ReasoningFeed } from "@/components/ReasoningFeed";
import { Reflection } from "@/components/Reflection";
import { StatusBar } from "@/components/StatusBar";
import { Section } from "@/components/Section";
import { SystemFlow } from "@/components/SystemFlow";
import { Card, CardContent } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ToolUsage } from "@/components/ToolUsage";
import { TradeHistoryTable } from "@/components/TradeHistoryTable";
import { formatDateTime } from "@/lib/format";
import { STAGES } from "@/lib/pipeline";
import { VALID_TABS, type TabId } from "@/lib/tabs";
import type {
  AccountState,
  AgentConfig,
  AssignmentEvent,
  Decision,
  EquityPoint,
  FunnelResponse,
  GreeksSnapshot,
  HealthBucket,
  HealthResponse,
  LlmUsageResponse,
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
      <span className="text-[10px] uppercase tracking-wide text-muted-foreground/70">Build</span>
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
      <div className="flex flex-wrap items-center justify-center gap-x-6 gap-y-1 border-t border-border/40 pt-3 text-xs">
        <span className="flex items-center gap-1.5" title="Last time this page fetched fresh data from the API">
          <Globe className="size-3.5" />
          <span className="text-[10px] uppercase tracking-wide text-muted-foreground/70">UI</span>
          {formatDateTime(frontendLastUpdated)}
        </span>
        <span className="flex items-center gap-1.5" title="Last completed backend trading-loop cycle">
          <Server className="size-3.5" />
          <span className="text-[10px] uppercase tracking-wide text-muted-foreground/70">Agent</span>
          {backendLastUpdated ? formatDateTime(backendLastUpdated) : "—"}
        </span>
        <span className="flex items-center gap-1.5" title="Timestamp of the most recent decision written to the database">
          <Database className="size-3.5" />
          <span className="text-[10px] uppercase tracking-wide text-muted-foreground/70">Data</span>
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
  funnel,
  trades,
  llmUsage,
  toolUsage,
  healthHistory,
  health,
  reflection,
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
  funnel: FunnelResponse | null;
  trades: Trade[] | null;
  llmUsage: LlmUsageResponse | null;
  toolUsage: ToolUsageResponse | null;
  healthHistory: HealthBucket[] | null;
  health: HealthResponse | null;
  reflection: ReflectionShape | null;
  frontendLastUpdated: string;
}) {
  const [tab, setTab] = useState<TabId>(initialTab);
  const walkCap = config ? Number(config.execution_guardrails.walk_cap_fraction) : null;

  // Tab switches never navigate -- everything was already fetched once on
  // load. Only the URL's ?tab= is updated (via the History API directly,
  // bypassing the Next.js router) so a reload or shared link restores the
  // same tab without re-running the page's data fetch.
  function handleTabChange(value: unknown) {
    const next = (VALID_TABS as readonly string[]).includes(value as string) ? (value as TabId) : "overview";
    setTab(next);
    // Only the tab param is rewritten -- ?decision= and ?gate= are the deep
    // link and have to survive a tab switch, which this used to discard by
    // rebuilding the whole query string from scratch.
    const url = new URL(window.location.href);
    if (next === "overview") url.searchParams.delete("tab");
    else url.searchParams.set("tab", next);
    window.history.replaceState(null, "", `${url.pathname}${url.search}`);
  }

  return (
    <main className="mx-auto w-full max-w-7xl p-4 font-mono text-base sm:p-8">
      {/* Layout rule: prose and chrome read at max-w-5xl, data gets the full
          max-w-7xl `main` allows -- applied per section, not per region, so a
          wide table can breathe while a paragraph never runs to 1280px. The
          header and footer are chrome; the Reflector's argument is prose;
          account, greeks, funnel, tables and the graph are data. */}
      <div className="mx-auto w-full max-w-5xl">
        <div className="mb-1 flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-3">
            <Image src="/logo.png" alt="" width={64} height={64} className="rounded-lg" />
            <h1 className="text-xl font-semibold sm:text-2xl">Autonomous Debate Trading Agent</h1>
          </div>
          <span className="text-sm text-muted-foreground">
            <LiveRefresh />
          </span>
        </div>
        <StatusBar status={status} />

        {/* Alert-like and reference material stay outside the tabs -- an
            assignment event matters regardless of which tab a judge is on. */}
        <AssignmentPanel events={assignments} />
      </div>

      <Tabs value={tab} onValueChange={handleTabChange}>
        <TabsList variant="line" className="mb-6 w-full">
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
          <TabsTrigger value="flow" className="gap-1.5">
            <Workflow className="size-3.5" />
            Pipeline
          </TabsTrigger>
          <TabsTrigger value="config" className="gap-1.5">
            <Settings className="size-3.5" />
            Config
          </TabsTrigger>
        </TabsList>

        {/* Overview: glanceable totals only -- account state, risk gauges,
            system health, and the entry-screening funnel. No raw rows here;
            drill-down content (decisions, trades) lives in its own tab. */}
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
            onOpenPipeline={() => handleTabChange("flow")}
          />
          {/* Greeks and funnel side by side because they describe one event:
              the delta breach is why the funnel's last two stages are zero.
              Stacked full-width, nothing connected them. */}
          <div className="grid gap-4 md:grid-cols-2">
            <GreeksGauges snapshot={greeksLatest} />
            <Funnel funnel={funnel} />
          </div>
          {/* Prose: a paragraph of argument, held to reading width. */}
          <div className="mx-auto w-full max-w-5xl">
            <Reflection
              reflection={reflection}
              variant="overview"
              onOpenDecisions={() => handleTabChange("decisions")}
            />
          </div>
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
          <ReasoningFeed
            decisions={decisions}
            walkCapFraction={walkCap}
            initialDecisionId={initialDecisionId}
            initialGates={initialGates}
          />
        </TabsContent>

        <TabsContent value="trades" className="flex flex-col gap-4">
          {/* The best single proof of execution engineering was three clicks
              deep inside an expanded table row. */}
          <FeaturedWalk trades={trades} walkCapFraction={walkCap} />
          <OpenPositionsTable positions={openPositions} assignments={assignments} />
          <TradeHistoryTable trades={trades} />
        </TabsContent>

        {/* Usage: cost and reliability only -- "is the agent healthy" lives in
            Overview's HealthStrip, not here. */}
        <TabsContent value="usage" className="flex flex-col gap-4">
          {/* The routing table leads: it is correct, it is live from /config,
              and it is the claim the aggregate below can only partly show --
              most of those calls predate per-node routing. */}
          <ModelEnsemble config={config} />
          <LlmUsage
            usage={llmUsage}
            ordersSent={trades?.length ?? 0}
            nodeModels={config?.llm.node_models}
          />
          <ToolUsage usage={toolUsage} />
        </TabsContent>

        {/* Same component, same STAGE_DEFS, higher detail -- one dataset, two
            presentations, so the two can never drift apart. Two datasets is
            exactly the trap that produced the eight points of content drift
            this rebuild fixes. */}
        <TabsContent value="flow" className="flex flex-col gap-4">
          <Section
            icon={Workflow}
            title="Pipeline"
            note="— every stage, every way a candidate can end"
            meta="drawn from agent/main.py, pipeline.py, gates.py and order_manager.py"
          >
            <SystemFlow detail="full" />
          </Section>
          <Card>
            <CardContent>
              <p className="mb-2 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
                Where each claim comes from
              </p>
              <ul className="grid gap-x-6 gap-y-1 text-xs text-muted-foreground sm:grid-cols-2">
                {STAGES.map((stage) => (
                  <li key={stage.key} className="flex justify-between gap-3">
                    <span className="text-foreground/80">{stage.title}</span>
                    <code className="text-right">{stage.source}</code>
                  </li>
                ))}
              </ul>
            </CardContent>
          </Card>
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

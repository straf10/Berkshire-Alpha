"use client";

import {
  ArrowLeftRight,
  Coins,
  Database,
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
import { DecisionsLog } from "@/components/DecisionsLog";
import { Funnel } from "@/components/Funnel";
import { GreeksGauges } from "@/components/GreeksGauges";
import { HealthStrip } from "@/components/HealthStrip";
import { LiveRefresh } from "@/components/LiveRefresh";
import { LlmUsage } from "@/components/LlmUsage";
import { OpenPositionsTable } from "@/components/OpenPositionsTable";
import { ReasoningFeed } from "@/components/ReasoningFeed";
import { Reflection } from "@/components/Reflection";
import { StatusBar } from "@/components/StatusBar";
import { SystemFlow } from "@/components/SystemFlow";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ToolUsage } from "@/components/ToolUsage";
import { TradeHistoryTable } from "@/components/TradeHistoryTable";
import { formatDateTime } from "@/lib/format";
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
        <a href="https://github.com/straf10" target="_blank" rel="noreferrer" className="hover:text-foreground">
          @straf10
        </a>{" "}
        &amp;{" "}
        <a href="https://github.com/stanimeros" target="_blank" rel="noreferrer" className="hover:text-foreground">
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
      </div>
    </footer>
  );
}

export function Dashboard({
  initialTab,
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

  // Tab switches never navigate -- everything was already fetched once on
  // load. Only the URL's ?tab= is updated (via the History API directly,
  // bypassing the Next.js router) so a reload or shared link restores the
  // same tab without re-running the page's data fetch.
  function handleTabChange(value: unknown) {
    const next = (VALID_TABS as readonly string[]).includes(value as string) ? (value as TabId) : "overview";
    setTab(next);
    const url = next === "overview" ? window.location.pathname : `${window.location.pathname}?tab=${next}`;
    window.history.replaceState(null, "", url);
  }

  return (
    <main className="mx-auto w-full max-w-7xl p-4 font-mono text-base sm:p-8">
      {/* Header locked to the same max-w-5xl as the footer -- only the tabs/
          content region below is allowed to use the wider max-w-7xl `main`
          gives it, so a wide table can breathe without the header/footer
          stretching to match. */}
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
            How it works
          </TabsTrigger>
          <TabsTrigger value="config" className="gap-1.5">
            <Settings className="size-3.5" />
            Config
          </TabsTrigger>
        </TabsList>

        {/* Overview: glanceable totals only -- account state, risk gauges,
            system health, and the entry-screening funnel. No raw rows here;
            drill-down content (decisions, trades) lives in its own tab. */}
        <TabsContent value="overview">
          <AccountVitals account={account} history={equityHistory} sessionDate={status.session_date} />
          <GreeksGauges snapshot={greeksLatest} />
          <Funnel funnel={funnel} />
          <HealthStrip buckets={healthHistory} />
        </TabsContent>

        {/* Decisions: intentionally both a compact skim table AND the full
            expandable reasoning feed for the SAME underlying decisions array --
            scan the log, then expand the matching card for the full debate/
            risk-vote chain, without hunting across tabs for one subject. */}
        <TabsContent value="decisions">
          <DecisionsLog decisions={decisions} />
          <ReasoningFeed decisions={decisions} />
          <Reflection reflection={reflection} />
        </TabsContent>

        <TabsContent value="trades">
          <OpenPositionsTable positions={openPositions} assignments={assignments} />
          <TradeHistoryTable trades={trades} />
          {!openPositions?.length && !trades?.length && (
            <p className="text-muted-foreground">No open positions or trade history yet.</p>
          )}
        </TabsContent>

        {/* Usage: cost and reliability only -- "is the agent healthy" lives in
            Overview's HealthStrip, not here. */}
        <TabsContent value="usage">
          <LlmUsage usage={llmUsage} />
          <ToolUsage usage={toolUsage} />
          {!llmUsage?.totals.calls && !toolUsage?.totals.calls && (
            <p className="text-muted-foreground">No usage data recorded yet this deploy.</p>
          )}
        </TabsContent>

        <TabsContent value="flow">
          <SystemFlow />
        </TabsContent>

        <TabsContent value="config">
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

"use client";

import "@xyflow/react/dist/style.css";

import {
  Background,
  BackgroundVariant,
  Controls,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  type Edge,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import {
  Activity,
  ArrowLeftRight,
  BarChart3,
  Filter,
  Gavel,
  ShieldCheck,
  Swords,
  Workflow,
  XCircle,
} from "lucide-react";
import type { ComponentType } from "react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";

type StageMode = "deterministic" | "llm" | "hybrid";

interface StageDef {
  icon: ComponentType<{ className?: string }>;
  title: string;
  mode: StageMode;
  description: string;
  reject?: string;
}

// A separate (non-indexed) StageDef feeds STAGE_DEFS below -- xyflow's
// Node<T> requires T to structurally satisfy Record<string, unknown>, but
// giving StageDef itself an index signature makes `keyof StageDef`
// collapse to `string`, which breaks Omit/Pick on it elsewhere.
interface StageData extends StageDef {
  rejects: boolean;
  [key: string]: unknown;
}

const MODE_LABEL: Record<StageMode, string> = {
  deterministic: "Deterministic",
  llm: "LLM",
  hybrid: "LLM + fallback",
};

const MODE_ACCENT: Record<StageMode, string> = {
  deterministic: "border-l-muted-foreground/50",
  llm: "border-l-primary",
  hybrid: "border-l-amber-500",
};

const MODE_BADGE: Record<StageMode, string> = {
  deterministic: "bg-muted text-foreground/70",
  llm: "bg-primary/15 text-primary",
  hybrid: "bg-amber-500/15 text-amber-400",
};

// Stage node: the main pipeline chain. `rejects: true` gives it a bottom
// source handle so a dashed reject edge can run down into the "no trade"
// terminal node.
function StageNode({ data }: NodeProps<Node<StageData>>) {
  const Icon = data.icon;
  return (
    <div
      className={`flex w-[210px] flex-col gap-1.5 rounded-md border border-border/60 border-l-4 bg-card p-2.5 shadow-sm ${MODE_ACCENT[data.mode]}`}
    >
      <Handle type="target" position={Position.Left} className="!bg-muted-foreground/50" />
      <div className="flex items-center gap-1.5">
        <Icon className="size-3.5 shrink-0 text-foreground/80" />
        <span className="text-xs font-semibold leading-tight">{data.title}</span>
      </div>
      <Badge className={`w-fit ${MODE_BADGE[data.mode]}`}>{MODE_LABEL[data.mode]}</Badge>
      <p className="text-[11px] leading-snug text-muted-foreground">{data.description}</p>
      {/* The reject list is the most interesting thing in the node, and it
          used to be truncated to one line with the full text in a native
          `title` -- not keyboard-reachable, absent on touch, unreliable to a
          screen reader. It wraps in the node body instead. */}
      {data.reject && (
        <p className="mt-auto text-[10px] leading-tight text-destructive/80">reject: {data.reject}</p>
      )}
      <Handle type="source" position={Position.Right} className="!bg-muted-foreground/50" />
      {data.rejects && (
        <Handle type="source" position={Position.Bottom} id="reject" className="!bg-destructive/70" />
      )}
    </div>
  );
}

function TerminalNode() {
  return (
    <div className="flex w-[210px] flex-col items-center justify-center gap-1 rounded-md border border-dashed border-destructive/50 bg-destructive/5 p-2.5 text-center">
      <Handle type="target" position={Position.Top} className="!bg-destructive/70" />
      <div className="flex items-center gap-1.5 text-destructive/90">
        <XCircle className="size-3.5" />
        <span className="text-xs font-semibold">No trade</span>
      </div>
      <p className="text-[10px] leading-snug text-muted-foreground">
        Logged as a decision row with its reject reason -- never reaches execution.
      </p>
    </div>
  );
}

const nodeTypes = { stage: StageNode, terminal: TerminalNode };

const STAGE_DEFS: StageDef[] = [
  {
    icon: Filter,
    title: "Universe screen",
    mode: "deterministic",
    description: "Full ticker universe scored against the macro/regime snapshot -- pure math, no model calls.",
    reject: "NOT_SHORTLISTED, NO_REGIME, DATA_NOT_OK, DEBIT_NO_MOMENTUM_CONFIRMATION",
  },
  {
    icon: BarChart3,
    title: "Analysts",
    mode: "llm",
    description: "Quant + News score each shortlisted candidate in parallel.",
    reject: "ANALYST_SCORE_BELOW_FLOOR, NOT_TOP_DEBATE_CANDIDATE",
  },
  {
    icon: Swords,
    title: "Debate",
    mode: "llm",
    description: "Bull vs. Bear argue 2 rounds -> consensus score + conviction multiplier.",
    reject: "LOW_CONVICTION (at the gate)",
  },
  {
    icon: Workflow,
    title: "Trader proposal",
    mode: "hybrid",
    description: "Model proposes a spread; unparsable proposals fall back to a deterministic builder.",
    reject: "STRIKE_NOT_IN_CHAIN, STRUCTURE_MISMATCH, LEG_COUNT, NOT_DEFINED_RISK, ...",
  },
  {
    icon: Gavel,
    title: "Risk team vote",
    mode: "llm",
    description: "Aggressive / Neutral / Conservative personas vote in parallel on the proposal.",
    reject: "RISK_TEAM_VETO",
  },
  {
    icon: ShieldCheck,
    title: "Deterministic gate",
    mode: "deterministic",
    description: "Buying power, position & greeks limits, drawdown kill-switch, earnings, DTE, cutoff -- no LLM.",
    reject: "MAX_CONCURRENT_POSITIONS, DRAWDOWN_TERMINAL, LOW_CONVICTION, NEGATIVE_EDGE, LLM_BUDGET_CEILING, ...",
  },
  {
    icon: ArrowLeftRight,
    title: "Execution",
    mode: "deterministic",
    description: "Approved plan is walked to fill on Alpaca's paper book -> becomes an open position.",
  },
  {
    icon: Activity,
    title: "Monitoring & exit",
    mode: "deterministic",
    description: "5-min ticks re-check greeks/exits; closes on stop, target, DTE, or the unwind date.",
  },
];

const STEP_X = 260;
const STAGE_Y = 0;
const TERMINAL_Y = 260;

const stageNodes: Node<StageData>[] = STAGE_DEFS.map((stage, i): Node<StageData> => ({
  id: `stage-${i}`,
  type: "stage",
  position: { x: i * STEP_X, y: STAGE_Y },
  data: { ...stage, rejects: Boolean(stage.reject) || [2, 5].includes(i) },
  draggable: true,
}));

// Debate (2) and the deterministic gate (5) also route rejects here even
// though their own `reject` label points elsewhere (LOW_CONVICTION is
// actually enforced at the gate; the gate's own reasons are summarized
// rather than listed) -- both still end a candidate the same way.
const BRANCH_INDICES = [0, 1, 2, 3, 4, 5];

const terminalX =
  (Math.min(...BRANCH_INDICES.map((i) => i * STEP_X)) + Math.max(...BRANCH_INDICES.map((i) => i * STEP_X))) / 2;

const terminalNode: Node = {
  id: "no-trade",
  type: "terminal",
  position: { x: terminalX, y: TERMINAL_Y },
  data: {},
  draggable: true,
};

const nodes: Node[] = [...stageNodes, terminalNode];

const mainEdges: Edge[] = STAGE_DEFS.slice(0, -1).map((_, i) => ({
  id: `main-${i}`,
  source: `stage-${i}`,
  target: `stage-${i + 1}`,
  type: "smoothstep",
  animated: true,
  style: { stroke: "var(--muted-foreground)", strokeWidth: 1.5 },
  markerEnd: { type: MarkerType.ArrowClosed, color: "var(--muted-foreground)" },
}));

const branchEdges: Edge[] = BRANCH_INDICES.map((idx) => ({
  id: `reject-${idx}`,
  source: `stage-${idx}`,
  sourceHandle: "reject",
  target: "no-trade",
  type: "smoothstep",
  style: { stroke: "var(--destructive)", strokeWidth: 1.5, strokeDasharray: "4 3", opacity: 0.7 },
  markerEnd: { type: MarkerType.ArrowClosed, color: "var(--destructive)" },
}));

const edges: Edge[] = [...mainEdges, ...branchEdges];

// GitHub-Actions-style, draggable node graph (React Flow): one horizontal
// main chain plus every LLM/gate stage's reject path fanning into a shared
// "no trade" terminal node. Nodes/edges are a fixed dataset -- this tab is
// architecture, not live state (live per-stage counts already live in
// Overview's Funnel) -- but React Flow's canvas still gives free drag,
// pan, zoom, and minimap instead of hand-computed SVG coordinates.
export function SystemFlow() {
  return (
    <Card>
      <CardContent className="h-[560px] w-full p-0 [&_.react-flow]:bg-transparent">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          fitView
          fitViewOptions={{ padding: 0.15 }}
          minZoom={0.4}
          maxZoom={1.5}
          proOptions={{ hideAttribution: true }}
        >
          <Background variant={BackgroundVariant.Dots} gap={16} size={1} color="var(--border)" />
          <Controls showInteractive={false} />
        </ReactFlow>
      </CardContent>
    </Card>
  );
}

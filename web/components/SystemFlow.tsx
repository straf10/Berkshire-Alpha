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
import { XCircle } from "lucide-react";
import { useMemo } from "react";
import { cn } from "@/lib/utils";
import {
  LANES,
  LANE_STAGES,
  RAIL_TEXT,
  STAGES,
  type LaneId,
  type StageDef,
  type StageKey,
  type StageMode,
} from "@/lib/pipeline";

// ---------------------------------------------------------------------------
// Geometry. Two layouts over one dataset (lib/pipeline.ts) -- `full` is the
// spec'd 1160x800 canvas that fits at zoom 1.0, `compact` drops the reject
// rails and the terminal so the same twelve stages fit above the fold on
// Overview. There is no second node array; both are derived here.
// ---------------------------------------------------------------------------

const COLS = [70, 340, 610, 880];
const NODE_W = 230;

interface Geometry {
  rows: [number, number, number];
  nodeH: number;
  laneY: (row: number) => number;
  laneH: number;
  railY: (row: number) => number;
  canvasH: number;
}

const GEOMETRY: Record<"full" | "compact", Geometry> = {
  full: {
    rows: [0, 240, 480],
    nodeH: 118,
    laneY: (row) => row - 38,
    laneH: 234,
    railY: (row) => row + 140,
    canvasH: 820,
  },
  compact: {
    rows: [0, 170, 340],
    nodeH: 96,
    laneY: (row) => row - 34,
    laneH: 164,
    railY: (row) => row + 120,
    canvasH: 500,
  },
};

const LANE_ROW: Record<LaneId, number> = { a: 0, b: 1, c: 2 };

const MODE_LABEL: Record<StageMode, string> = {
  deterministic: "DETERMINISTIC",
  llm: "LLM",
  hybrid: "LLM + FALLBACK",
};

// Semantic, not decorative: --primary is the agent acting, --warn is a
// fallback path that needs watching, --muted-foreground is arithmetic.
const MODE_ACCENT: Record<StageMode, string> = {
  deterministic: "bg-muted-foreground",
  llm: "bg-primary",
  hybrid: "bg-warn",
};

const MODE_TEXT: Record<StageMode, string> = {
  deterministic: "text-muted-foreground",
  llm: "text-primary",
  hybrid: "text-warn",
};

// ---------------------------------------------------------------------------
// Node types
// ---------------------------------------------------------------------------

interface StageNodeData extends Record<string, unknown> {
  stage: StageDef;
  detail: "full" | "compact";
  /** Replay: this stage has already played. */
  lit: boolean;
  /** Replay: this stage is playing now. */
  active: boolean;
  /** Replay: this stage did not run, and why. */
  skippedReason: string | null;
  /** A replay is in progress, so unplayed stages should recede. */
  replaying: boolean;
  hasRail: boolean;
}

function StageNode({ data }: NodeProps<Node<StageNodeData>>) {
  const { stage, detail, lit, active, skippedReason, replaying, hasRail } = data;
  const full = detail === "full";

  return (
    <div
      className={cn(
        "relative flex h-full w-full flex-col gap-1 overflow-hidden rounded-md border bg-card p-2.5 transition-colors",
        active && "border-primary ring-2 ring-primary/40",
        !active && lit && "border-primary/50",
        !active && !lit && "border-hairline",
        skippedReason && "border-dashed border-idle bg-transparent",
        replaying && !lit && !active && !skippedReason && "opacity-40"
      )}
    >
      <Handle type="target" position={Position.Left} className="!bg-muted-foreground/50" />
      {/* The mode rule, drawn as a rail down the left edge rather than a
          border-left utility, so it survives the border colour changing
          under replay state. */}
      <span className={cn("absolute inset-y-0 left-0 w-[3px] rounded-l-md", MODE_ACCENT[stage.mode])} />
      <p className="text-[12.5px] font-semibold leading-tight">{stage.title}</p>
      <p className={cn("text-[9px] font-semibold uppercase tracking-widest", MODE_TEXT[stage.mode])}>
        {MODE_LABEL[stage.mode]}
      </p>
      <p className="text-[10px] leading-snug text-muted-foreground">{stage.description}</p>
      {skippedReason ? (
        <p className="mt-auto text-[9px] font-semibold uppercase tracking-wide text-idle">
          {skippedReason}
        </p>
      ) : (
        full &&
        stage.rejects.length > 0 && (
          <p className="mt-auto text-[9px] leading-tight text-neg/80">
            {stage.rejects.slice(0, 3).join(" · ")}
            {stage.rejects.length > 3 && ` · +${stage.rejects.length - 3} more`}
          </p>
        )
      )}
      <Handle type="source" position={Position.Right} className="!bg-muted-foreground/50" />
      {hasRail && <Handle type="source" position={Position.Bottom} id="reject" className="!bg-neg/70" />}
    </div>
  );
}

interface LaneNodeData extends Record<string, unknown> {
  label: string;
  annotation: string;
  shortCircuit: string | null;
}

function LaneNode({ data }: NodeProps<Node<LaneNodeData>>) {
  return (
    <div
      className={cn(
        "h-full w-full rounded-lg border bg-surface-2/55",
        data.shortCircuit ? "border-dashed border-warn/60" : "border-hairline"
      )}
    >
      <div className="flex items-baseline justify-between gap-3 px-3.5 pt-2">
        <span className="text-[10px] font-bold uppercase tracking-[0.14em] text-muted-foreground">
          {data.label}
          {data.shortCircuit && <span className="ml-2 text-warn">· SHORT-CIRCUIT</span>}
        </span>
        <span className="text-[10px] text-muted-foreground/80">
          {data.shortCircuit ?? data.annotation}
        </span>
      </div>
    </div>
  );
}

function RailNode({ data }: NodeProps<Node<{ text: string } & Record<string, unknown>>>) {
  return (
    <div className="relative flex h-full w-full items-center rounded-md border border-dashed border-neg/30 bg-neg/[0.06] px-3.5">
      {[11.06, 37.02, 62.98, 88.94].map((left, i) => (
        <Handle
          key={i}
          type="target"
          id={`t${i}`}
          position={Position.Top}
          style={{ left: `${left}%` }}
          className="!bg-neg/60"
        />
      ))}
      <span className="text-[11px] text-neg/95">↳ {data.text}</span>
      <Handle type="source" position={Position.Left} id="bus" className="!bg-neg/60" />
    </div>
  );
}

function TerminalNode() {
  return (
    <div className="flex h-full w-full flex-col items-center justify-center gap-0.5 rounded-md border border-dashed border-neg/45 bg-neg/[0.07] p-2 text-center">
      <Handle type="target" position={Position.Top} className="!bg-neg/70" />
      <div className="flex items-center gap-1.5 text-neg">
        <XCircle className="size-3.5" />
        <span className="text-[12.5px] font-bold">NO TRADE</span>
      </div>
      <p className="text-[10.5px] leading-snug text-muted-foreground">
        A decisions row is still written, with its reason. Nothing is silent.
      </p>
    </div>
  );
}

const nodeTypes = { stage: StageNode, lane: LaneNode, rail: RailNode, terminal: TerminalNode };

// ---------------------------------------------------------------------------
// Graph construction
// ---------------------------------------------------------------------------

const EDGE_MUTED = { stroke: "var(--muted-foreground)", strokeWidth: 1.6, opacity: 0.85 };
const EDGE_REJECT = { stroke: "var(--neg)", strokeWidth: 1.3, strokeDasharray: "4 3", opacity: 0.65 };

export interface SystemFlowProps {
  /** `full` adds the reject vocabulary and the rails; `compact` is the shape of the work. */
  detail?: "full" | "compact";
  /** Replay: stages that have already played. */
  lit?: ReadonlySet<StageKey>;
  /** Replay: the stage playing now. */
  active?: StageKey | null;
  /** Replay: stages that did not run this cycle, and why. */
  skipped?: Partial<Record<StageKey, string>>;
  /** Set when the LLM lane was short-circuited, e.g. "gate short-circuit: REDUCE_ONLY". */
  laneBShortCircuit?: string | null;
  /** True while a replay is running, so unplayed stages recede. */
  replaying?: boolean;
  className?: string;
}

export function SystemFlow({
  detail = "full",
  lit,
  active = null,
  skipped,
  laneBShortCircuit = null,
  replaying = false,
  className,
}: SystemFlowProps) {
  const geo = GEOMETRY[detail];
  const full = detail === "full";

  const { nodes, edges } = useMemo(() => {
    const nodes: Node[] = [];
    const edges: Edge[] = [];

    // Lane backgrounds first, at zIndex 0, so the stages sit on top of them.
    for (const lane of LANES) {
      const row = geo.rows[LANE_ROW[lane.id]];
      nodes.push({
        id: `lane-${lane.id}`,
        type: "lane",
        position: { x: 50, y: geo.laneY(row) },
        style: { width: 1080, height: geo.laneH },
        data: {
          label: lane.label,
          annotation: lane.annotation,
          shortCircuit: lane.id === "b" ? laneBShortCircuit : null,
        },
        selectable: false,
        draggable: false,
        zIndex: 0,
      });
    }

    for (const stage of STAGES) {
      const row = geo.rows[LANE_ROW[stage.lane]];
      const hasRail = full && stage.rejects.length > 0;
      nodes.push({
        id: stage.key,
        type: "stage",
        position: { x: COLS[stage.col], y: row },
        style: { width: NODE_W, height: geo.nodeH },
        data: {
          stage,
          detail,
          lit: lit?.has(stage.key) ?? false,
          active: active === stage.key,
          skippedReason: skipped?.[stage.key] ?? null,
          replaying,
          hasRail,
        } satisfies StageNodeData,
        selectable: false,
        draggable: false,
        zIndex: 1,
      });
    }

    // Main flow: along each lane's mid-line, then one short vertical hop
    // between lanes. Lane B runs right-to-left precisely so both hops are
    // short verticals rather than a return sweep across the canvas.
    for (const lane of LANES) {
      const ordered = LANE_STAGES[lane.id]
        .slice()
        .sort((x, y) => (lane.id === "b" ? y.col - x.col : x.col - y.col));
      for (let i = 0; i < ordered.length - 1; i++) {
        edges.push({
          id: `flow-${ordered[i].key}`,
          source: ordered[i].key,
          target: ordered[i + 1].key,
          type: "smoothstep",
          style: EDGE_MUTED,
          markerEnd: { type: MarkerType.ArrowClosed, color: "var(--muted-foreground)" },
        });
      }
    }
    // shortlist -> analysts, risk -> gate.
    for (const [source, target] of [
      ["shortlist", "analysts"],
      ["risk", "gate"],
    ] as const) {
      edges.push({
        id: `hop-${source}`,
        source,
        target,
        type: "smoothstep",
        style: EDGE_MUTED,
        markerEnd: { type: MarkerType.ArrowClosed, color: "var(--muted-foreground)" },
      });
    }

    // Reject rails, and the bus that joins all three to the terminal. Only in
    // `full`: the vocabulary is the whole point of the Pipeline tab, and it is
    // more than Overview needs.
    if (full) {
      for (const lane of LANES) {
        const row = geo.rows[LANE_ROW[lane.id]];
        nodes.push({
          id: `rail-${lane.id}`,
          type: "rail",
          position: { x: 70, y: geo.railY(row) },
          style: { width: 1040, height: 42 },
          data: { text: RAIL_TEXT[lane.id] },
          selectable: false,
          draggable: false,
          zIndex: 1,
        });
      }
      for (const stage of STAGES) {
        if (stage.rejects.length === 0) continue;
        edges.push({
          id: `reject-${stage.key}`,
          source: stage.key,
          sourceHandle: "reject",
          target: `rail-${stage.lane}`,
          targetHandle: `t${stage.col}`,
          type: "straight",
          style: EDGE_REJECT,
          markerEnd: { type: MarkerType.ArrowClosed, color: "var(--neg)" },
        });
      }

      nodes.push({
        id: "no-trade",
        type: "terminal",
        position: { x: 460, y: geo.rows[2] + 226 },
        style: { width: 260, height: 76 },
        data: {},
        selectable: false,
        draggable: false,
        zIndex: 1,
      });
      for (const lane of LANES) {
        edges.push({
          id: `bus-${lane.id}`,
          source: `rail-${lane.id}`,
          sourceHandle: "bus",
          target: "no-trade",
          type: "smoothstep",
          style: { ...EDGE_REJECT, opacity: 0.5 },
          markerEnd: { type: MarkerType.ArrowClosed, color: "var(--neg)" },
        });
      }
    }

    return { nodes, edges };
  }, [geo, full, detail, lit, active, skipped, laneBShortCircuit, replaying]);

  return (
    <div
      className={cn("w-full [&_.react-flow]:bg-transparent", className)}
      style={{ height: geo.canvasH }}
    >
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        // Was fitView + minZoom 0.4, which shrank 11px body text to
        // illegibility to get a 2,080px ribbon on screen. The canvas is now
        // sized to fit at 1.0, so 1.0 is where it opens.
        defaultViewport={{ x: 0, y: 0, zoom: 1 }}
        minZoom={0.5}
        maxZoom={1.6}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable={false}
        proOptions={{ hideAttribution: true }}
      >
        <Background variant={BackgroundVariant.Dots} gap={16} size={1} color="var(--border)" />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}

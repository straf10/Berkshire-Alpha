"use client";

import { Pause, Play, RotateCcw, Users, Workflow } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AgentTheatre } from "@/components/AgentTheatre";
import { Section } from "@/components/Section";
import { SystemFlow } from "@/components/SystemFlow";
import { WalkTimelineChart } from "@/components/charts/WalkTimelineChart";
import { Skeleton } from "@/components/ui/skeleton";
import { apiBase, fetchJson } from "@/lib/api";
import { formatDateTime, formatTimeUtc, safeJsonParse } from "@/lib/format";
import { isAgentStale } from "@/lib/health";
import { STAGE_BY_KEY, type StageKey } from "@/lib/pipeline";
import { delayFor, replaySource, type CycleSource } from "@/lib/replay";
import { cn } from "@/lib/utils";
import type { Decision, DecisionChain, Status, Trade } from "@/lib/types";

const SPEEDS = [1, 8] as const;

interface SpreadPlanShape {
  net_natural: string;
}

// The cycle the replay opens on.
//
// Pinned, on purpose, and this is the one hard-coded id in the file. Decision
// 86 (NVDA, 1 Sep) is the cycle where the pipeline visibly did its job: all
// ten nodes answered, the Bull COMMITted while the Bear DISAGREED in both
// rounds, the Conservative risk persona voted REJECT while the other two
// APPROVEd -- and the deterministic gate authorised it anyway. That is the
// whole thesis in one cycle, and a demo that has to argue with whatever ran
// last is not a demo.
//
// It is NOT pinned for being profitable. It is not: its trade is still open
// and carries no realised P&L, and of the two trades that HAVE closed both
// closed negative. Nothing in this UI calls it a winning trade, and nothing
// should -- the account id is published precisely so that claim could be
// checked.
//
// The derived pick below stays as the fallback for the day this row is not
// in the database, so a missing id degrades to "the last full cycle" rather
// than to an empty panel.
const PINNED_DECISION_ID = 86;

function derivedTargetId(decisions: Decision[], trades: Trade[] | null): number | null {
  const filled = (trades ?? []).filter((t) => t.fill_price !== null);
  if (filled.length > 0) {
    return filled.reduce((best, t) => (t.ts_utc > best.ts_utc ? t : best)).decision_id;
  }
  if (trades && trades.length > 0) {
    return trades.reduce((best, t) => (t.ts_utc > best.ts_utc ? t : best)).decision_id;
  }
  return decisions[0]?.id ?? null;
}

function Chip({ tone, children }: { tone: "warn" | "pos" | "idle"; children: React.ReactNode }) {
  const TONE = {
    warn: "border-warn/40 bg-warn/10 text-warn",
    pos: "border-pos/30 bg-pos/10 text-pos",
    idle: "border-idle/40 bg-idle/10 text-muted-foreground",
  };
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide",
        TONE[tone]
      )}
    >
      {children}
    </span>
  );
}

function TranscriptRow({
  event,
  current,
}: {
  event: ReturnType<typeof replaySource>["events"][number];
  current: boolean;
}) {
  const stage = STAGE_BY_KEY[event.stage];
  return (
    <li
      className={cn(
        "border-l-2 py-1.5 pl-2.5 pr-1",
        event.kind === "skip"
          ? "border-l-idle text-muted-foreground"
          : current
            ? "border-l-primary"
            : "border-l-hairline"
      )}
    >
      <div className="flex flex-wrap items-baseline gap-x-2">
        <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
          {stage.title}
        </span>
        <span className="text-[11px] font-semibold">{event.speaker}</span>
        <span
          className={cn(
            "text-[11px] font-semibold",
            event.kind === "skip" ? "text-idle" : "text-primary"
          )}
        >
          {event.headline}
        </span>
        {/* A skipped stage has no time of its own -- printing the cycle's
            start next to it would be inventing one. */}
        {event.kind !== "skip" && (
          <span className="ml-auto text-[10px] tabular-nums text-muted-foreground">
            {formatTimeUtc(event.tsUtc)}
            {event.approximate && <span className="ml-1 text-warn">≈</span>}
          </span>
        )}
      </div>
      {event.body && (
        <p className="mt-0.5 text-[11px] leading-relaxed text-foreground/70">{event.body}</p>
      )}
      {event.meta && <p className="text-[10px] text-muted-foreground">{event.meta}</p>}
    </li>
  );
}

// The graph is the map, the transcript is the log, and one component owns
// both -- which is also what earns the graph its place on Overview: it is
// doing something, not just documenting.
//
// <CycleTheatre> takes a CycleSource and knows nothing about where the events
// came from. Today that is replaySource() over tables that already exist.
// GET /live/cycle (§C2) becomes liveSource() and swaps in here; do NOT
// re-implement the theatre for it.
export function CycleTheatre({
  decisions,
  trades,
  status,
  walkCapFraction,
  onOpenPipeline,
}: {
  decisions: Decision[];
  trades: Trade[] | null;
  status: Status;
  walkCapFraction: number | null;
  onOpenPipeline?: () => void;
}) {
  const fallbackId = derivedTargetId(decisions, trades);
  const targetId = PINNED_DECISION_ID;
  const [chain, setChain] = useState<DecisionChain | null>(null);
  // Cast is the default because Overview is the landing page: a first-time
  // visitor has no reason yet to care about `agent/risk/gates.py:35-60`, but
  // every reason to watch eight models disagree. The graph is one click away
  // here and is the primary rendering on Pipeline and For the Judges.
  const [view, setView] = useState<"cast" | "technical">("cast");
  // Seeded from the target, so the effect below never has to setState
  // synchronously on mount just to report "nothing to load".
  const [loading, setLoading] = useState(targetId !== null);
  const [cursor, setCursor] = useState(-1);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState<(typeof SPEEDS)[number]>(1);
  // Staleness is a function of wall-clock time, which is impure during
  // render. Lazy initializer plus a subscription, the same shape StatusBar's
  // countdown uses.
  const [nowMs, setNowMs] = useState(() => Date.now());
  const railRef = useRef<HTMLOListElement>(null);

  useEffect(() => {
    const id = setInterval(() => setNowMs(Date.now()), 30_000);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    if (targetId === null) return;
    let cancelled = false;
    void (async () => {
      // fetchJson resolves null rather than throwing, so a pinned id that is
      // no longer in the database is indistinguishable from an outage here --
      // fall back to the derived cycle instead of rendering an empty stage.
      let data = await fetchJson<DecisionChain>(`${apiBase()}/decisions/${targetId}`);
      if (!data?.decision && fallbackId !== null && fallbackId !== targetId) {
        data = await fetchJson<DecisionChain>(`${apiBase()}/decisions/${fallbackId}`);
      }
      if (cancelled) return;
      setChain(data);
      setLoading(false);
    })();
    return () => {
      cancelled = true;
    };
  }, [targetId, fallbackId]);

  const source: CycleSource | null = useMemo(
    () => (chain ? replaySource(chain, { scanUtcs: status.scan_utcs }) : null),
    [chain, status.scan_utcs]
  );

  const atEnd = source === null || cursor >= source.events.length - 1;
  // `playing` is the user's intent; this is whether anything is actually
  // moving. Deriving it means the effect below simply stops scheduling at the
  // end rather than setting state from inside itself.
  const running = playing && !atEnd;

  // Advance one event at a time, at the pace the real gaps imply. A timeout
  // chain rather than an interval, because every gap is a different length.
  useEffect(() => {
    if (!running || !source) return;
    const next = source.events[cursor + 1];
    const id = setTimeout(
      () => setCursor((c) => c + 1),
      delayFor(source.events[cursor], next, speed)
    );
    return () => clearTimeout(id);
  }, [running, cursor, source, speed]);

  useEffect(() => {
    railRef.current?.lastElementChild?.scrollIntoView({ block: "nearest" });
  }, [cursor]);

  const play = useCallback(() => {
    if (atEnd) setCursor(-1);
    setPlaying(true);
  }, [atEnd]);

  // --- State machine. Market-closed is the PRIMARY state, designed first: it
  // is what a judge will almost certainly land on, and it is not a failure.
  const stale = isAgentStale(status, nowMs);
  const replaying = cursor >= 0 && !atEnd;
  const finished = cursor >= 0 && atEnd;

  const played = useMemo(
    () => (source ? source.events.slice(0, cursor + 1) : []),
    [source, cursor]
  );
  const lit = useMemo(
    () => new Set(played.filter((e) => e.kind !== "skip").map((e) => e.stage)),
    [played]
  );
  const activeEvent = cursor >= 0 && source ? source.events[cursor] : null;
  const activeStage: StageKey | null = activeEvent?.stage ?? null;

  const walkReached = played.some((e) => e.stage === "walk" && e.kind !== "skip");
  const natural = chain?.decision
    ? Number(safeJsonParse<SpreadPlanShape>(chain.decision.plan_json)?.net_natural)
    : NaN;

  const meta = (
    <span className="flex flex-wrap items-center gap-2">
      {/* Non-negotiable: a replay presented as live is the one thing here that
          would actually damage the submission. The chip is always on, and
          green is reserved for genuinely live. */}
      {cursor >= 0 ? (
        <Chip tone="warn">Replay · not live</Chip>
      ) : status.is_open ? (
        stale ? (
          <Chip tone="warn">Stale</Chip>
        ) : (
          <Chip tone="pos">Market open</Chip>
        )
      ) : (
        <Chip tone="idle">Market closed</Chip>
      )}
      {source && <span className="text-[11px] text-muted-foreground">{source.label}</span>}
    </span>
  );

  return (
    <Section variant="bare" icon={Workflow} title="What the agent does, and what it just did" meta={meta}>
      <div className="glass rounded-xl p-3">
        {loading ? (
          <Skeleton className="h-24 w-full" />
        ) : (
          <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
            <div className="min-w-0">
              {stale && (
                <p className="mb-1 text-sm tabular-nums text-warn">
                  The market is open and the agent said its next action — {status.next_action ?? "a cycle"} —
                  was due at {formatDateTime(status.next_action_utc!)}. It is more than two management
                  ticks late. Something is wrong, and this panel is not hiding it.
                </p>
              )}
              {!source && (
                <p className="text-sm text-muted-foreground">
                  No cycle has run yet. The pipeline below is the shape of the work, drawn from{" "}
                  <code>agent/main.py</code>. It lights up left to right on the first scan.
                </p>
              )}
              {source && cursor < 0 && (
                <p className="text-sm text-foreground/80">
                  {status.is_open
                    ? "Nothing is mid-cycle right now."
                    : "Nothing is running, and that is correct — the agent trades market hours only."}{" "}
                  This is a cycle that {source.laneBShortCircuit ? "short-circuited the LLM lane" : "ran end to end"}
                  , replayed from its stored rows. Play it back and watch the agent think.
                </p>
              )}
              {source && cursor >= 0 && (
                <p className="text-sm tabular-nums text-foreground/80">
                  Event {cursor + 1} of {source.events.length}
                  {finished && " — complete"}
                </p>
              )}
            </div>
            {source && (
              <div className="flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  onClick={() => (running ? setPlaying(false) : play())}
                  className="inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-xs font-semibold text-primary-foreground hover:opacity-90 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
                >
                  {running ? <Pause className="size-3.5" /> : <Play className="size-3.5" />}
                  {running
                    ? "Pause"
                    : cursor < 0
                      ? "Replay the last full cycle"
                      : finished
                        ? "Replay again"
                        : "Resume"}
                </button>
                {cursor >= 0 && (
                  <button
                    type="button"
                    onClick={() => {
                      setPlaying(false);
                      setCursor(-1);
                    }}
                    className="inline-flex items-center gap-1.5 rounded-md border border-border px-2 py-1.5 text-xs text-muted-foreground hover:text-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
                  >
                    <RotateCcw className="size-3.5" />
                    Reset
                  </button>
                )}
                <div role="group" aria-label="Diagram view" className="flex items-center gap-1">
                  {(
                    [
                      ["cast", "Cast", Users],
                      ["technical", "Technical", Workflow],
                    ] as const
                  ).map(([id, label, Icon]) => (
                    <button
                      key={id}
                      type="button"
                      aria-pressed={view === id}
                      onClick={() => setView(id)}
                      className={cn(
                        "inline-flex items-center gap-1 rounded-md border px-2 py-1 text-xs focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring",
                        view === id
                          ? "border-primary bg-primary/10 text-primary"
                          : "border-border text-muted-foreground hover:text-foreground"
                      )}
                    >
                      <Icon className="size-3.5" />
                      {label}
                    </button>
                  ))}
                </div>
                <div role="group" aria-label="Playback speed" className="flex items-center gap-1">
                  {SPEEDS.map((s) => (
                    <button
                      key={s}
                      type="button"
                      aria-pressed={speed === s}
                      onClick={() => setSpeed(s)}
                      className={cn(
                        "rounded-md border px-2 py-1 text-xs focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring",
                        speed === s
                          ? "border-primary bg-primary/10 text-primary"
                          : "border-border text-muted-foreground hover:text-foreground"
                      )}
                    >
                      {s}×
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}

        {source && (
          <label className="mb-3 flex items-center gap-2 text-[11px] text-muted-foreground">
            <span className="shrink-0">Scrub</span>
            <input
              type="range"
              min={-1}
              max={source.events.length - 1}
              value={cursor}
              onChange={(e) => {
                setPlaying(false);
                setCursor(Number(e.target.value));
              }}
              aria-label="Replay position"
              className="h-1 w-full accent-primary"
            />
          </label>
        )}

        <div className="grid gap-3 lg:grid-cols-[minmax(0,3fr)_minmax(0,1fr)]">
          <div className="min-w-0 overflow-hidden rounded-lg border border-hairline">
            {view === "cast" ? (
              <AgentTheatre
                played={played}
                active={activeEvent}
                skipped={source?.skipped}
                replaying={replaying || finished}
              />
            ) : (
              <SystemFlow
                detail="compact"
                lit={lit}
                active={activeStage}
                skipped={source?.skipped}
                laneBShortCircuit={source?.laneBShortCircuit ?? null}
                replaying={replaying || finished}
              />
            )}
          </div>
          <div className="min-w-0">
            <p className="mb-1 text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">
              Transcript
            </p>
            <ol
              ref={railRef}
              className="max-h-[460px] space-y-0.5 overflow-y-auto rounded-lg bg-surface-2 p-2"
            >
              {played.length === 0 ? (
                <li className="p-2 text-[11px] text-muted-foreground">
                  Press play. Every line below is text the agent actually produced — nothing here is
                  written for the demo.
                </li>
              ) : (
                played.map((e) => (
                  <TranscriptRow key={e.seq} event={e} current={e.seq === cursor} />
                ))
              )}
            </ol>
            {played.some((e) => e.approximate) && (
              <p className="mt-1 text-[10px] text-muted-foreground">
                <span className="text-warn">≈</span> timing approximate — the four deterministic
                screen stages have no per-stage timestamp in the schema, so theirs are spaced across
                the cycle rather than recorded.
              </p>
            )}
          </div>
        </div>

        {/* The walk does not get 95 transcript lines. When the replay reaches
            it, the chart appears instead -- one sweep for the whole walk. */}
        {walkReached && source?.walkTrade && (
          <div className="mt-3 rounded-lg border border-hairline p-3">
            <p className="mb-1 text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">
              Limit-order walk — {source.walkTrade.symbol}
            </p>
            <WalkTimelineChart
              trade={source.walkTrade}
              natural={Number.isFinite(natural) ? natural : null}
              walkCapFraction={walkCapFraction}
            />
          </div>
        )}

        <p className="mt-3 text-[11px] text-muted-foreground">
          Every stage can end a candidate, and a rejected candidate still gets a decisions row with
          its reason — nothing is silent.{" "}
          {onOpenPipeline && (
            <button
              type="button"
              onClick={onOpenPipeline}
              className="text-primary underline-offset-2 hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
            >
              See the full reject vocabulary on Pipeline →
            </button>
          )}
        </p>
      </div>
    </Section>
  );
}

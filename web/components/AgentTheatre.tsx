"use client";

import {
  Briefcase,
  Gavel,
  Newspaper,
  Scale,
  Shield,
  Sigma,
  TrendingDown,
  TrendingUp,
  Zap,
  type LucideIcon,
} from "lucide-react";
import Image from "next/image";
import { useMemo } from "react";
import { PERSONAS, type PersonaDef, type PersonaId, type StageKey } from "@/lib/pipeline";
import type { StageEvent } from "@/lib/replay";
import { cn } from "@/lib/utils";

// The cast view: the same replay, drawn as the eight personas that actually
// speak rather than as twelve boxes.
//
// This is a SECOND rendering of one dataset, not a second dataset -- it takes
// the events <CycleTheatre> already has and derives everything from them. It
// deliberately does NOT replace <SystemFlow>: the graph carries the reject
// vocabulary, the lane structure and the `agent/...py:line` provenance, which
// is what a judge who reads needs. This carries what a judge who watches
// needs. Both are reachable from the same panel.
//
// Nothing here invents state. A persona is drawn as having spoken only when
// an event with its id has played, and the headline in its bubble is the
// model's own `doc_action` / vote / verdict string.

const ICON: Record<PersonaId, LucideIcon> = {
  QUANT: Sigma,
  NEWS: Newspaper,
  BULL: TrendingUp,
  BEAR: TrendingDown,
  TRADER: Briefcase,
  RISK_CONSERVATIVE: Shield,
  RISK_NEUTRAL: Scale,
  RISK_AGGRESSIVE: Zap,
};

// Rows in pipeline order. Lane B runs right-to-left on the graph; here it
// reads top-to-bottom, which is the direction a transcript reads.
const ROWS: { stage: StageKey; title: string; ids: PersonaId[] }[] = [
  { stage: "analysts", title: "Analysts — in parallel", ids: ["QUANT", "NEWS"] },
  { stage: "debate", title: "Debate — Bull vs Bear, 2 rounds", ids: ["BULL", "BEAR"] },
  { stage: "trader", title: "Trader", ids: ["TRADER"] },
  {
    stage: "risk",
    title: "Risk team — one shared model, on purpose",
    ids: ["RISK_CONSERVATIVE", "RISK_NEUTRAL", "RISK_AGGRESSIVE"],
  },
];

const PERSONA_BY: Record<PersonaId, PersonaDef> = Object.fromEntries(
  PERSONAS.map((p) => [p.id, p])
) as Record<PersonaId, PersonaDef>;

type Phase = "waiting" | "speaking" | "spoke" | "skipped";

interface PersonaState {
  phase: Phase;
  /** The model's own word for what it did: COMMIT, DISAGREE, APPROVE, REJECT. */
  headline: string | null;
  /** The model this persona actually ran on in the replayed cycle. */
  model: string | null;
  skippedReason: string | null;
}

// A vote or a debate turn that went against the trade. Drawn with --warn,
// which is the token for "a risk state worth attention" -- not with --neg,
// which on this dashboard means a negative number and nothing else.
const DISSENT = new Set(["REJECT", "DISAGREE", "VETO"]);

// StageEvent.meta is `${model} · ${latency}ms` (lib/replay.ts callMeta). The
// model half is the one thing on this stage that must come from the replayed
// call rather than from config: routing changed on 2 Sep and older cycles ran
// a single model for every node.
function modelOf(event: StageEvent | undefined): string | null {
  return event?.meta?.split(" · ")[0] ?? null;
}

function Avatar({
  persona,
  state,
}: {
  persona: PersonaDef;
  state: PersonaState;
}) {
  const Icon = ICON[persona.id];
  const speaking = state.phase === "speaking";
  const dissent = state.headline !== null && DISSENT.has(state.headline.toUpperCase());

  return (
    <div className="flex w-[96px] shrink-0 flex-col items-center gap-1">
      {/* The bubble is reserved whether or not it is filled, so a persona
          speaking does not shove the whole row down by 22px. */}
      <div className="flex h-[20px] items-end">
        {speaking && state.headline && (
          <span className="animate-in fade-in zoom-in-95 max-w-[96px] truncate rounded-md bg-primary px-1.5 py-0.5 text-[10px] font-semibold text-primary-foreground">
            {state.headline}
          </span>
        )}
      </div>

      <div
        className={cn(
          "relative grid size-[56px] place-items-center rounded-full border bg-surface-2 transition-all duration-300",
          speaking && "scale-110 border-primary ring-2 ring-primary/40",
          state.phase === "spoke" && (dissent ? "border-warn/60" : "border-primary/40"),
          state.phase === "waiting" && "border-hairline opacity-40",
          state.phase === "skipped" && "border-dashed border-idle opacity-30"
        )}
      >
        <Image
          src="/translucent_bg_v2.png"
          alt=""
          width={48}
          height={48}
          className="pointer-events-none select-none"
        />
        <span
          className={cn(
            "absolute -bottom-0.5 -right-0.5 grid size-[19px] place-items-center rounded-full border border-hairline bg-card",
            speaking ? "text-primary" : dissent && state.phase === "spoke" ? "text-warn" : "text-muted-foreground"
          )}
        >
          <Icon className="size-3" />
        </span>
      </div>

      <p
        className={cn(
          "text-[10.5px] font-semibold leading-tight",
          state.phase === "waiting" || state.phase === "skipped" ? "text-muted-foreground" : "text-foreground"
        )}
      >
        {persona.label}
      </p>
      {/* The verdict stays visible after the bubble goes -- otherwise the
          Conservative's REJECT vanishes before the gate that overruled it. */}
      <p
        className={cn(
          "h-[13px] text-[9px] font-semibold uppercase tracking-wide",
          dissent ? "text-warn" : "text-muted-foreground"
        )}
      >
        {state.phase === "spoke" ? state.headline : state.phase === "skipped" ? "did not run" : ""}
      </p>
    </div>
  );
}

export interface AgentTheatreProps {
  /** Events already played, in replay order. */
  played: StageEvent[];
  /** The event playing now, or null before play / after reset. */
  active: StageEvent | null;
  /** Stages that did not run this cycle, and why. */
  skipped?: Partial<Record<StageKey, string>>;
  /** True once a replay has started, so unplayed personas recede. */
  replaying?: boolean;
  className?: string;
}

export function AgentTheatre({
  played,
  active,
  skipped = {},
  replaying = false,
  className,
}: AgentTheatreProps) {
  const states = useMemo(() => {
    const out = {} as Record<PersonaId, PersonaState>;
    for (const p of PERSONAS) {
      // Last event wins: the Bull speaks twice and round 2 is what stands.
      const mine = played.filter((e) => e.persona === p.id && e.kind !== "skip");
      const last = mine[mine.length - 1];
      const skippedReason = skipped[p.stage] ?? null;
      out[p.id] = {
        phase:
          active?.persona === p.id
            ? "speaking"
            : last
              ? "spoke"
              : skippedReason
                ? "skipped"
                : "waiting",
        headline: last?.headline ?? null,
        model: modelOf(last),
        skippedReason,
      };
    }
    return out;
  }, [played, active, skipped]);

  // The gate is not a persona and must not be drawn as one -- it is the thing
  // none of them can argue with. It gets the widest element on the stage.
  const gateEvent = useMemo(
    () => [...played].reverse().find((e) => e.stage === "gate" && e.kind !== "skip") ?? null,
    [played]
  );
  const gateActive = active?.stage === "gate";
  const approved = gateEvent?.headline === "APPROVED";

  const debateLive = active?.stage === "debate";
  const speaking = active?.persona ? PERSONA_BY[active.persona] : null;
  const speakingModel = modelOf(active ?? undefined) ?? speaking?.model ?? null;

  // Said out loud rather than left for a judge to catch: on a cycle recorded
  // before per-node routing shipped, the Bull and the Bear ran the SAME model,
  // and the transcript beside this says so on every line. Derived from the
  // replayed calls, so it disappears by itself on any cycle from 2 Sep on.
  const sharedDebateModel =
    states.BULL.model !== null && states.BULL.model === states.BEAR.model ? states.BULL.model : null;

  return (
    <div className={cn("flex flex-col gap-2 p-3", className)}>
      {/* Lane A gets one line, not four avatars. Saying "no speaker" out loud
          is the honest version of leaving it off the stage. */}
      <p className="text-center text-[10px] uppercase tracking-widest text-muted-foreground">
        Screen · chain · regime · shortlist —{" "}
        <span className="normal-case tracking-normal">arithmetic, no speaker</span>
      </p>

      {ROWS.map((row) => (
        <div key={row.stage} className="flex flex-col gap-1">
          <p className="text-center text-[9px] font-semibold uppercase tracking-widest text-muted-foreground">
            {row.title}
          </p>
          <div className="relative flex flex-wrap items-start justify-center gap-x-4 gap-y-2">
            {row.ids.map((id) => (
              <Avatar key={id} persona={PERSONA_BY[id]} state={states[id]} />
            ))}
            {/* Bull <-> Bear. One dashed line that animates only while the
                debate stage is the one playing. */}
            {row.stage === "debate" && (
              <svg
                aria-hidden
                // 20px bubble slot + 4px gap + half of the 56px head = the vertical
                // centre of the two faces it joins.
                className="pointer-events-none absolute left-1/2 top-[52px] h-2 w-[44px] -translate-x-1/2"
                viewBox="0 0 46 8"
              >
                <line
                  x1="1"
                  y1="4"
                  x2="45"
                  y2="4"
                  strokeWidth="1.5"
                  strokeDasharray="4 3"
                  className={cn(
                    debateLive ? "stroke-primary" : "stroke-muted-foreground/40",
                    debateLive && "animate-[dash_0.6s_linear_infinite]"
                  )}
                />
              </svg>
            )}
          </div>
          {row.stage === "debate" && sharedDebateModel && (
            <p className="text-center text-[9.5px] leading-snug text-warn">
              Both sides ran {sharedDebateModel} on this cycle. Per-node routing —
              DeepSeek-V3.1-Terminus for the Bull, Kimi-K2-Instruct for the Bear — shipped on 2 Sep,
              after this one was recorded.
            </p>
          )}
        </div>
      ))}

      <div
        className={cn(
          "flex items-center gap-2.5 rounded-lg border px-3 py-2 transition-all duration-300",
          gateActive && "ring-2 ring-primary/40",
          gateEvent
            ? approved
              ? "border-primary/60 bg-primary/[0.07]"
              : "border-neg/50 bg-neg/[0.06]"
            : cn("border-hairline", replaying && "opacity-40")
        )}
      >
        <Gavel className={cn("size-4 shrink-0", gateEvent && !approved ? "text-neg" : "text-primary")} />
        <div className="min-w-0">
          <p className="text-[11px] font-bold uppercase tracking-wide">
            Risk gate —{" "}
            <span className={gateEvent && !approved ? "text-neg" : "text-primary"}>
              {gateEvent?.headline ?? "deterministic, not a vote"}
            </span>
          </p>
          <p className="text-[10px] leading-snug text-muted-foreground">
            {gateEvent
              ? approved
                ? "Every persona above is advisory. This is the only thing that can authorise an order — and it does so on arithmetic, whatever the votes said."
                : "The gate ended the candidate. A decisions row is still written, with this reason."
              : "Runs after the votes, on arithmetic. No model output can bypass it."}
          </p>
        </div>
      </div>

      {/* One caption slot, so the panel does not resize as the replay moves. */}
      <p className="min-h-[26px] text-[10px] leading-snug text-muted-foreground">
        {speaking ? (
          <>
            <span className="font-semibold text-foreground">{speaking.label}</span> ·{" "}
            {speakingModel}
            {" — "}
            {speaking.role}
          </>
        ) : (
          "Eight personas speak, and each prints the model it actually ran on. Everything else on this cycle is arithmetic."
        )}
      </p>
    </div>
  );
}

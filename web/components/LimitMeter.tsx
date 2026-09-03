import { formatMoney } from "@/lib/format";

// Under $100 the whole-dollar formatMoney flattens a real number to "$0"
// (portfolio vega is currently -$0.40), so small readings keep two decimals.
function formatExposure(value: number): string {
  if (Math.abs(value) >= 100) return formatMoney(value);
  return value.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 2 });
}

function formatPctOfLimit(pct: number): string {
  if (pct >= 10) return `${Math.round(pct)}%`;
  if (pct >= 1) return `${pct.toFixed(1)}%`;
  return `${pct.toFixed(2)}%`;
}

// A risk limit is a threshold, not a maximum -- the meter has to be able to
// draw a breach. The bar chart this replaced set its domain to
// max(100, actual), so 100% of limit and 220% of limit rendered as the same
// full bar: the single most dramatic number the agent produced was invisible.
//
// Here the track runs to max(120%, actual), the limit is drawn as an explicit
// tick, and everything past it is red. Positioned divs, no chart library --
// which also drops recharts from this part of the client bundle.
export function LimitMeter({
  label,
  value,
  limit,
  compact = false,
}: {
  label: string;
  value: number;
  limit: number;
  /** A fifth of the height: 0.02% of limit deserves a fifth of the ink. */
  compact?: boolean;
}) {
  const magnitude = Math.abs(value);
  const pct = limit > 0 ? (magnitude / limit) * 100 : 0;
  const scaleMax = Math.max(120, pct);
  const fill = (pct / scaleMax) * 100;
  const tick = (100 / scaleMax) * 100;
  const safe = Math.min(fill, tick);
  const over = Math.max(0, fill - tick);

  const headroom = limit - magnitude;
  const direction = value < 0 ? "net short" : value > 0 ? "net long" : "flat";
  const headroomText =
    headroom < 0 ? `${formatExposure(-headroom)} over` : `${formatExposure(headroom)} of headroom`;

  return (
    <div>
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5">
        <span className="font-semibold">{label}</span>
        <span className="tabular-nums">
          {formatExposure(value)} <span className="text-muted-foreground">({direction})</span>
        </span>
      </div>
      <div
        role="img"
        aria-label={`${label} exposure ${formatExposure(value)}, ${formatPctOfLimit(pct)} of the plus-or-minus ${formatExposure(limit)} limit — ${headroomText}`}
        className={`relative mt-1.5 w-full overflow-hidden rounded-sm bg-muted ${compact ? "h-1" : "h-5"}`}
      >
        <div className="absolute inset-y-0 left-0 bg-primary/80" style={{ width: `${safe}%` }} />
        {over > 0 && (
          <div className="absolute inset-y-0 bg-destructive" style={{ left: `${tick}%`, width: `${over}%` }} />
        )}
        {/* The limit itself. Drawn on top of the fill so a breach reads as
            "past the line" rather than "a longer bar". */}
        <div className="absolute inset-y-0 w-px bg-foreground/80" style={{ left: `${tick}%` }} />
      </div>
      <div className="mt-1 flex flex-wrap items-baseline justify-between gap-x-3 text-[11px] text-muted-foreground">
        <span className={headroom < 0 ? "font-semibold text-red-400" : undefined}>
          {formatPctOfLimit(pct)} of limit · {headroomText}
        </span>
        <span>limit ±{formatExposure(limit)}</span>
      </div>
    </div>
  );
}

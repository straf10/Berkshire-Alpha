import { Scale } from "lucide-react";
import { Section, SectionHero } from "@/components/Section";
import { SectionEmpty } from "@/components/SectionEmpty";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatDateTime, formatMoney, formatSignedMoney } from "@/lib/format";
import type { MarkGapResponse } from "@/lib/types";

// The one section on this dashboard that reports on the BROKER rather than on
// the agent. Everything else here is our own conduct; this is the number the
// competition reads, checked against the only bound that needs no model: a
// vertical spread's value is confined by its own strikes.
//
// The caveat below is not decoration. A markgap proves the mark is impossible;
// it does not prove the difference is collectible. Anyone who reads this panel
// as "we are secretly up by $X" has read it wrong, so the panel says so in its
// own body copy rather than leaving it to a footnote in the report.
export function MarkGapPanel({ markgap }: { markgap: MarkGapResponse | null }) {
  if (markgap === null || markgap.value === undefined) {
    return (
      <SectionEmpty
        icon={Scale}
        title="Mark integrity"
        reason="No mark-integrity reading yet. The management tick checks every open spread's broker mark against the bounds its own strikes permit, every five minutes while the market is open."
      />
    );
  }

  const { spreads, total_markgap, omitted, computed_at, intrinsic_spot_source } = markgap.value;
  const total = Number(total_markgap);
  const flat = spreads.length === 0;

  return (
    <Section
      icon={Scale}
      title="Mark integrity"
      meta={`as of ${formatDateTime(markgap.asof ?? computed_at, { seconds: true })}`}
    >
      <SectionHero
        value={flat ? "Flat" : formatSignedMoney(total)}
        suffix={
          flat
            ? "no open spreads to mark"
            : "of reported P&L the strikes forbid"
        }
        tone={flat ? "idle" : total === 0 ? "pos" : "warn"}
      />

      <p className="mt-3 max-w-[70ch] text-sm leading-relaxed text-muted-foreground">
        A vertical spread cannot be worth less than nothing, nor more than the
        distance between its strikes. Judged equity is{" "}
        <span className="text-foreground">cash + the broker&apos;s mark</span>, and the
        broker&apos;s mark is under no such obligation — on a wide or stale chain it can
        land outside the band the position&apos;s own strikes permit. This is that
        distance, and only that distance.{" "}
        <span className="font-semibold text-foreground">
          A gap proves the mark is impossible. It does not prove the difference is
          collectible
        </span>{" "}
        — what a market maker will pay at the close on a 50%-wide chain is a separate
        question, and a worse one.
      </p>

      {flat ? (
        <p className="mt-4 rounded-md border-l-2 border-hairline bg-surface-2 py-2 pl-2.5 pr-2 text-xs leading-relaxed text-foreground/80">
          The book is flat, so there is nothing to mark and the reading is zero by
          construction — not by luck. The last non-zero reading and its timestamp stay
          in the header above rather than disappearing with the position.
        </p>
      ) : (
        <div className="mt-4 overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Spread</TableHead>
                <TableHead className="text-right">Broker mark</TableHead>
                <TableHead className="text-right">Permitted band</TableHead>
                <TableHead className="text-right">Intrinsic</TableHead>
                <TableHead className="text-right">Gap</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {spreads.map((s) => {
                const gap = Number(s.markgap);
                return (
                  <TableRow key={s.trade_id}>
                    <TableCell className="whitespace-nowrap">
                      <span className="font-semibold">{s.symbol}</span>{" "}
                      <span className="text-xs text-muted-foreground">
                        {s.qty}x {s.width} wide
                      </span>
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {formatSignedMoney(Number(s.broker_mark))}
                    </TableCell>
                    <TableCell className="text-right tabular-nums text-muted-foreground">
                      {formatMoney(Number(s.band_low))} to {formatMoney(Number(s.band_high))}
                    </TableCell>
                    <TableCell className="text-right tabular-nums text-muted-foreground">
                      {s.intrinsic === null ? "—" : formatMoney(Number(s.intrinsic))}
                    </TableCell>
                    <TableCell
                      className={`text-right tabular-nums ${gap === 0 ? "text-muted-foreground" : "font-semibold text-warn"}`}
                    >
                      {gap === 0 ? "none" : formatSignedMoney(gap)}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </div>
      )}

      <p className="mt-3 text-caption tabular-nums text-muted-foreground">
        Intrinsic is computed from the {intrinsic_spot_source}&apos;s spot, so it lags the
        tape; the band and the gap need no spot at all.
        {omitted > 0 && (
          <>
            {" "}
            {omitted} spread{omitted === 1 ? "" : "s"} omitted — a spread is only bounded
            when both legs are held in the size the trade recorded, so a partial
            assignment or an unreported leg is skipped rather than guessed at.
          </>
        )}
      </p>
    </Section>
  );
}

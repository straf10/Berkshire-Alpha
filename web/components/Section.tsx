import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { cn } from "@/lib/utils";

// Three section archetypes, not one.
//
// Before this, every section on the page was the same object: a <Card>, an
// uppercase muted title, and one widget. The treatment appeared in thirteen
// files, which is why the dashboard read as generated -- nothing could be
// more important than anything else, because everything was shaped the same.
//
//   card   bordered, one elevation up from the page. For data you read.
//   bare   no chrome at all. For things that should feel like the page
//          rather than an object on it -- the pipeline graph, hero strips.
//   quote  --surface-2 plus an accent rule. For the one place on this
//          dashboard where a different voice is speaking: the Reflector.
//
// Every section still shares the same header, so the archetypes read as one
// family rather than three unrelated components.
export type SectionVariant = "card" | "bare" | "quote";

export function SectionHeader({
  icon: Icon,
  title,
  note,
  meta,
}: {
  icon?: LucideIcon;
  title: ReactNode;
  /** Normal-case qualifier that belongs to the title, e.g. a session date. */
  note?: ReactNode;
  /** Right-aligned provenance: row counts, "filtered client-side", a stamp. */
  meta?: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
      <p className="flex flex-wrap items-center gap-1.5 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
        {Icon && <Icon className="size-3.5" />}
        {title}
        {note && <span className="normal-case">{note}</span>}
      </p>
      {meta && <span className="text-[11px] text-muted-foreground">{meta}</span>}
    </div>
  );
}

// One hero number per section, and only one. `page` is Overview's Account --
// the single largest number on the site; `section` is everything else.
// Uniform density was the third mechanism behind the generated look: when
// every number is 14px, none of them is the answer.
export function SectionHero({
  value,
  suffix,
  tone = "neutral",
  size = "section",
  className,
}: {
  value: ReactNode;
  /** Trailing context at reading size: "of 200 entered", "calls". */
  suffix?: ReactNode;
  tone?: "neutral" | "pos" | "neg" | "primary" | "warn" | "idle";
  size?: "page" | "section";
  className?: string;
}) {
  const TONE: Record<string, string> = {
    neutral: "text-foreground",
    pos: "text-pos",
    neg: "text-neg",
    primary: "text-primary",
    warn: "text-warn",
    idle: "text-idle",
  };
  return (
    <p
      className={cn(
        "font-semibold tabular-nums leading-none",
        size === "page" ? "text-[52px]" : "text-[34px]",
        TONE[tone],
        className
      )}
    >
      {value}
      {suffix && (
        <span className="ml-2 text-base font-normal leading-normal text-muted-foreground">
          {suffix}
        </span>
      )}
    </p>
  );
}

export function Section({
  variant = "card",
  icon,
  title,
  note,
  meta,
  children,
  className,
}: {
  variant?: SectionVariant;
  icon?: LucideIcon;
  title: ReactNode;
  note?: ReactNode;
  meta?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  const header = <SectionHeader icon={icon} title={title} note={note} meta={meta} />;

  if (variant === "bare") {
    return (
      <section className={className}>
        <div className="mb-2">{header}</div>
        {children}
      </section>
    );
  }

  if (variant === "quote") {
    return (
      <section
        className={cn(
          "rounded-lg border border-l-2 border-hairline border-l-accent bg-surface-2 p-4",
          className
        )}
      >
        <div className="mb-2">{header}</div>
        {children}
      </section>
    );
  }

  return (
    <Card className={className}>
      <CardHeader className="pb-2">
        <CardTitle>{header}</CardTitle>
      </CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  );
}

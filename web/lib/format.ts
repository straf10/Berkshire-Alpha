const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

// Every timestamp from the API is a UTC ISO string (agent/storage's ts_utc
// columns) -- formats manually in UTC rather than toLocaleString() so
// output is identical whether this runs on the server (Page's Footer) or
// the client (DecisionCard etc.), instead of drifting with server locale/TZ.
export function formatDateTime(iso: string, opts: { seconds?: boolean } = {}): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  const hh = String(d.getUTCHours()).padStart(2, "0");
  const mm = String(d.getUTCMinutes()).padStart(2, "0");
  const time = opts.seconds ? `${hh}:${mm}:${String(d.getUTCSeconds()).padStart(2, "0")}` : `${hh}:${mm}`;
  return `${MONTHS[d.getUTCMonth()]} ${d.getUTCDate()}, ${time} UTC`;
}

// Time-of-day only, same manual-UTC discipline as formatDateTime -- for the
// session window ("13:30-20:00 UTC"), where the date is already established.
export function formatTimeUtc(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return `${String(d.getUTCHours()).padStart(2, "0")}:${String(d.getUTCMinutes()).padStart(2, "0")}`;
}

// Day and month only, same manual-UTC discipline -- for a chip or label that
// has to disambiguate two sessions ("2 Sep 14:15") without spending a whole
// formatDateTime on it.
export function formatDayMonth(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]}`;
}

export function formatMoney(value: number | string): string {
  const n = typeof value === "string" ? Number(value) : value;
  if (!Number.isFinite(n)) return "—";
  return n.toLocaleString(undefined, {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  });
}

// formatMoney rounds to whole dollars, which flattens LLM per-call/per-node
// costs (fractions of a cent to a few dollars) to $0. This keeps 2-4
// significant decimal digits instead, scaling precision down as the amount
// grows so a $1234.56 total doesn't print four decimal places.
export function formatCost(value: number): string {
  if (!Number.isFinite(value)) return "—";
  const digits = value >= 1 ? 2 : value >= 0.01 ? 3 : 4;
  return value.toLocaleString(undefined, {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export function formatSignedMoney(value: number): string {
  if (!Number.isFinite(value)) return "—";
  const sign = value > 0 ? "+" : "";
  return `${sign}${formatMoney(value)}`;
}

export function formatPct(fraction: number, digits = 1): string {
  if (!Number.isFinite(fraction)) return "—";
  return `${(fraction * 100).toFixed(digits)}%`;
}

export function formatCountdown(targetIso: string, nowMs: number = Date.now()): string {
  const ms = new Date(targetIso).getTime() - nowMs;
  if (ms <= 0) return "any moment now";
  const totalMinutes = Math.round(ms / 60000);
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  if (hours === 0) return `${minutes}m`;
  return `${hours}h ${minutes}m`;
}

export function daysToExpiry(expiry: string, from: Date = new Date()): number {
  const exp = new Date(`${expiry}T00:00:00Z`);
  const today = new Date(Date.UTC(from.getUTCFullYear(), from.getUTCMonth(), from.getUTCDate()));
  return Math.round((exp.getTime() - today.getTime()) / 86_400_000);
}

// ENTER is --primary, not green: it is the agent's own action, which is the
// one thing --primary is reserved for. --pos/--neg are P&L sign only, and an
// entry is not a profit. HALT is --destructive because something stopped the
// agent, and NO_TRADE is --idle: it ran as designed and chose not to trade.
export function actionColor(action: string): string {
  if (action === "ENTER") return "text-primary";
  if (action === "HALT") return "text-destructive";
  return "text-muted-foreground";
}

export function actionBadgeVariant(action: string): "default" | "destructive" | "secondary" {
  if (action === "ENTER") return "default";
  if (action === "HALT") return "destructive";
  return "secondary";
}

export function modeLabel(mode: string): string {
  if (mode === "llm") return "LLM";
  if (mode === "llm-degraded") return "LLM (degraded)";
  // The debate chose the name; spread_builder.build() chose the strikes after
  // the trader model failed validation twice.
  if (mode === "llm-fallback") return "LLM + deterministic strikes";
  return "quant-only";
}

export function docActionVariant(action: string): "default" | "destructive" | "secondary" {
  return action === "COMMIT" ? "default" : "destructive";
}

export function riskDecisionVariant(decision: string): "default" | "destructive" | "secondary" {
  if (decision === "APPROVE") return "default";
  if (decision === "REJECT") return "destructive";
  return "secondary";
}

// Reflector verdict pill: LOOSEN reads as an accent call-out, TIGHTEN as a
// warning, HOLD as the neutral default.
export function verdictVariant(verdict: string): "default" | "destructive" | "secondary" {
  if (verdict === "LOOSEN") return "default";
  if (verdict === "TIGHTEN") return "destructive";
  return "secondary";
}

// LLM_NODE_MODELS (agent/config.py) stores full provider/model ids
// ("deepseek-ai/DeepSeek-V3.1-Terminus") -- the provider prefix is noise
// next to a stage name in the reasoning feed, so this keeps only the model.
export function formatModelName(model: string): string {
  const idx = model.lastIndexOf("/");
  return idx === -1 ? model : model.slice(idx + 1);
}

export function safeJsonParse<T>(raw: string | null | undefined): T | null {
  if (!raw) return null;
  try {
    return JSON.parse(raw) as T;
  } catch {
    return null;
  }
}

interface LegShape {
  strike: number;
  right: "C" | "P";
  side: "BUY" | "SELL";
}

export function compactLegs(legsJson: string): string {
  const legs = safeJsonParse<LegShape[]>(legsJson) ?? [];
  if (legs.length === 0) return "—";
  const strikes = legs.map((l) => l.strike).join("/");
  const right = legs[0]?.right ?? "";
  return `${strikes} ${right}`;
}

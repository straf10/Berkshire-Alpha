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

export function actionColor(action: string): string {
  if (action === "ENTER") return "text-emerald-400";
  if (action === "HALT") return "text-red-400";
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

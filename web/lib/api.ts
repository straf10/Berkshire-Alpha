export function apiBase(): string {
  return process.env.NEXT_PUBLIC_API_BASE!;
}

/**
 * Never throws. Every dashboard section fetches independently through this
 * so one missing/erroring endpoint blanks only that section instead of
 * tripping the page's global ServiceDown fallback (docs/day6_ui_plan.md S7.4).
 */
export async function fetchJson<T>(url: string): Promise<T | null> {
  try {
    const res = await fetch(url, { cache: "no-store" });
    if (!res.ok) return null;
    return (await res.json()) as T;
  } catch {
    return null;
  }
}

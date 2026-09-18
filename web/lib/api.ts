export function apiBase(): string {
  return process.env.NEXT_PUBLIC_API_BASE!;
}

/**
 * Never throws. Every dashboard section fetches independently through this
 * so one missing/erroring endpoint blanks only that section instead of
 * tripping the page's global ServiceDown fallback.
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

/**
 * For endpoints that answer `{}` when the agent has not written that state
 * yet (/state/account, /greeks/latest, /markgap) -- on a fresh database every
 * one of them does. Sections treat `null` as "nothing yet", so an empty object
 * must not reach them as if it were a snapshot.
 */
export async function fetchState<T>(url: string): Promise<T | null> {
  const value = await fetchJson<T>(url);
  if (value !== null && typeof value === "object" && Object.keys(value).length === 0) return null;
  return value;
}

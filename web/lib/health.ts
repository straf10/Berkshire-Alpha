import type { Status } from "@/lib/types";

// agent/config.py MANAGEMENT_INTERVAL_S. The trading loop republishes /status
// once per iteration (main._publish_status), so this is the cadence at which
// next_action_utc is refreshed while the agent is alive.
const MANAGEMENT_INTERVAL_MS = 300_000;

// Two consecutive missed publishes before the agent is called stale -- one
// late tick is a cold Railway container, not an outage.
export const STALE_GRACE_MS = 2 * MANAGEMENT_INTERVAL_MS;

/**
 * Is the agent late for a deadline it set itself?
 *
 * The rule this replaced compared `now` against `/health`'s `last_cycle_utc`
 * with the same 10-minute window, on the stated assumption that a management
 * tick refreshes that stamp. It does not: `last_cycle` is written only at the
 * end of `scan_cycle` (agent/main.py), and entry scans are 90 minutes apart.
 * So the warning fired for the 45 minutes between the open and the first scan
 * of the day -- reporting yesterday's last scan as evidence something was
 * wrong -- and again for ~80 minutes of every 90-minute gap after it. Roughly
 * 90% of a session, all of it false.
 *
 * `next_action_utc` is the signal that actually means what the panel claims.
 * The agent clamps it to `max(scheduled, now_utc)` (main._next_action), so
 * while the loop is alive it is never in the past; if the loop dies, /status
 * freezes and the stamp ages past the grace window on its own. Both real
 * failures are caught, and being idle on schedule is not one of them.
 */
export function isAgentStale(status: Status, nowMs: number): boolean {
  if (status.is_open !== true) return false;
  if (!status.next_action_utc) return false;
  const dueMs = Date.parse(status.next_action_utc);
  if (Number.isNaN(dueMs)) return false;
  return nowMs - dueMs > STALE_GRACE_MS;
}

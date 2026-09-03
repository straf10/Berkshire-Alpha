// US equity regular session, in UTC. The agent's own session plan comes from
// Alpaca's calendar (agent/session.py), but /health/history buckets carry no
// market flag, so past hours have to be classified here until C1 adds one.
const OPEN_MIN = 13 * 60 + 30; // 13:30 UTC = 09:30 ET
const CLOSE_MIN = 20 * 60; //     20:00 UTC = 16:00 ET
const HOUR_MS = 3_600_000;

function utcDate(d: Date): string {
  return d.toISOString().slice(0, 10);
}

/**
 * True when the hour `[bucketStart, bucketStart+1h)` overlaps a regular
 * trading session.
 *
 * `openUtc`/`closeUtc` are `status.open_utc`/`status.close_utc`, which come
 * from Alpaca's real calendar and are authoritative -- but only for the one
 * session they describe, so they are used when the bucket falls on that date
 * and the weekday assumption covers the rest.
 *
 * Deliberately NOT derived from the samples themselves ("an hour is open if
 * it sits between that session's first and last sample"): that is circular,
 * because a whole-session outage produces no samples, so the derived window
 * would be empty and the outage would paint as healthy.
 */
export function isMarketHour(bucketStartUtc: string, openUtc?: string, closeUtc?: string): boolean {
  const start = new Date(bucketStartUtc);
  if (Number.isNaN(start.getTime())) return false;
  const end = new Date(start.getTime() + HOUR_MS);

  if (openUtc && closeUtc) {
    const open = new Date(openUtc);
    const close = new Date(closeUtc);
    if (!Number.isNaN(open.getTime()) && !Number.isNaN(close.getTime()) && utcDate(start) === utcDate(open)) {
      return end > open && start < close;
    }
  }

  const day = start.getUTCDay();
  if (day === 0 || day === 6) return false;
  const startMin = start.getUTCHours() * 60 + start.getUTCMinutes();
  return startMin + 60 > OPEN_MIN && startMin < CLOSE_MIN;
}

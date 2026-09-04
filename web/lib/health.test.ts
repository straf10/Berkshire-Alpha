import { describe, expect, it } from "vitest";
import { isAgentStale, STALE_GRACE_MS } from "@/lib/health";
import type { Status } from "@/lib/types";

// A real session, taken from GET /status on 2026-09-04: the market opens at
// 13:30 UTC, the agent publishes four entry scans 90 minutes apart, and the
// first of them is 45 minutes after the open.
const OPEN = Date.parse("2026-09-04T13:30:00Z");
const SCAN_1 = Date.parse("2026-09-04T14:15:00Z");
const SCAN_2 = Date.parse("2026-09-04T15:45:00Z");

function status(over: Partial<Status> = {}): Status {
  return {
    is_open: true,
    session_date: "2026-09-04",
    open_utc: "2026-09-04T13:30:00+00:00",
    close_utc: "2026-09-04T20:00:00+00:00",
    scan_utcs: [
      "2026-09-04T14:15:00+00:00",
      "2026-09-04T15:45:00+00:00",
      "2026-09-04T17:15:00+00:00",
      "2026-09-04T18:45:00+00:00",
    ],
    completed_scans: 0,
    next_action: "entry scan 1",
    next_action_utc: "2026-09-04T14:15:00+00:00",
    ...over,
  };
}

describe("isAgentStale", () => {
  // The regression this function exists for. The old rule compared `now`
  // against /health's last_cycle_utc with a 10-minute window, so from the
  // moment the market opened until the first scan ran it announced that
  // something was wrong and cited *yesterday's* last scan as the evidence.
  it("is quiet between the open and the first scan of the day", () => {
    expect(isAgentStale(status(), OPEN + 60_000)).toBe(false);
    expect(isAgentStale(status(), SCAN_1 - 60_000)).toBe(false);
  });

  // The old rule's second failure mode: entry scans are 90 minutes apart, so
  // it fired for ~80 minutes of every gap between them.
  it("is quiet in the 90-minute gap between two scans", () => {
    const between = status({ completed_scans: 1, next_action: "entry scan 2", next_action_utc: "2026-09-04T15:45:00+00:00" });
    expect(isAgentStale(between, SCAN_1 + 80 * 60_000)).toBe(false);
    expect(isAgentStale(between, SCAN_2 - 60_000)).toBe(false);
  });

  it("tolerates one missed publish, and reports two", () => {
    expect(isAgentStale(status(), SCAN_1 + STALE_GRACE_MS - 1)).toBe(false);
    expect(isAgentStale(status(), SCAN_1 + STALE_GRACE_MS + 1)).toBe(true);
  });

  // If the loop dies, /status stops being republished, so next_action_utc
  // freezes and ages out on its own. This is the case the panel is for.
  it("reports an agent whose status has frozen since yesterday", () => {
    const yesterday = status({ next_action_utc: "2026-09-03T17:19:50+00:00" });
    expect(isAgentStale(yesterday, SCAN_1)).toBe(true);
  });

  it("never fires while the market is closed", () => {
    const closed = status({ is_open: false, next_action_utc: "2026-09-03T17:19:50+00:00" });
    expect(isAgentStale(closed, SCAN_1)).toBe(false);
    expect(isAgentStale(status({ is_open: undefined }), SCAN_1 + 86_400_000)).toBe(false);
  });

  // An older API build, or a loop that has not published yet: absent data is
  // not evidence of failure, and a panel that cries wolf on it is worse than
  // one that stays quiet.
  it("stays quiet on missing or unparseable timing", () => {
    expect(isAgentStale(status({ next_action_utc: undefined }), SCAN_1 + 86_400_000)).toBe(false);
    expect(isAgentStale(status({ next_action_utc: "not a date" }), SCAN_1 + 86_400_000)).toBe(false);
  });
});

import { describe, expect, it } from "vitest";
import type { SessionView } from "../protocol.gen";
import { formatClock, remainingS } from "./timer";

// A 25-minute Focus Session that started at epoch 1000 s.
const session = (over: Partial<SessionView> = {}): SessionView => ({
  task: "Email",
  started_at: 1000,
  duration_s: 1500,
  paused_since: null,
  paused_total_s: 0,
  time_up_at: null,
  blind: null,
  episodes: 0,
  nudges: 0,
  ...over,
});

describe("the time remaining from the snapshot's raw session fields", () => {
  it("counts down from the Duration as the Focus Session clock does", () => {
    expect(remainingS(session(), 1000)).toBe(1500);
    expect(remainingS(session(), 1600)).toBe(900);
  });

  it("holds still during a Pause", () => {
    const paused = session({ paused_since: 1300 });
    expect(remainingS(paused, 1300)).toBe(1200);
    expect(remainingS(paused, 1900)).toBe(1200);
  });

  it("leaves out the time already spent paused", () => {
    // Paused 1300..1420, resumed: 2 minutes don't count.
    expect(remainingS(session({ paused_total_s: 120 }), 1600)).toBe(1020);
  });

  it("stops at zero when time is up, through the extend grace", () => {
    expect(remainingS(session(), 2600)).toBe(0);
    expect(remainingS(session({ time_up_at: 2500 }), 2700)).toBe(0);
  });

  it("never reads more than the Duration, even before the start", () => {
    expect(remainingS(session(), 990)).toBe(1500);
  });

  it("has no time remaining without a session", () => {
    expect(remainingS(null, 1000)).toBeNull();
  });
});

describe("the clock face", () => {
  it("shows minutes and seconds, rounding up so it reads 0:00 only at the end", () => {
    expect(formatClock(1500)).toBe("25:00");
    expect(formatClock(599.2)).toBe("10:00");
    expect(formatClock(61)).toBe("1:01");
    expect(formatClock(0.4)).toBe("0:01");
    expect(formatClock(0)).toBe("0:00");
  });

  it("shows hours for sessions over an hour", () => {
    expect(formatClock(5400)).toBe("1:30:00");
  });
});

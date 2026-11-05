import { describe, expect, it } from "vitest";
import type { Envelope, SessionView } from "../protocol.gen";
import { buildTimeline, mergeHistory } from "./timeline";

const T0 = 1_000;
const session: SessionView = {
  task: "Email",
  started_at: T0,
  duration_s: 600,
  paused_since: null,
  paused_total_s: 0,
  time_up_at: null,
  blind: null,
};

let seq = 0;
const at = (t: number, type: string, data: object): Envelope => ({ seq: ++seq, t, type, data }) as Envelope;
const attention = (t: number, was: string, state: string) => at(t, "attention", { was, state });
const tool = (t: number, name: string, result: object) => at(t, "tool_call", { name, args: {}, result });

describe("buildTimeline", () => {
  it("lays a segment per Attention State from the session's start to now", () => {
    const history = [
      at(T0, "session.started", { task: "Email", duration_min: 10 }),
      attention(T0 + 30, "focused", "phone_use"),
      attention(T0 + 60, "phone_use", "focused"),
    ];
    expect(buildTimeline(history, session, T0 + 100).segments).toEqual([
      { state: "focused", from: 0, to: 30 },
      { state: "phone_use", from: 30, to: 60 },
      { state: "focused", from: 60, to: 100 },
    ]);
  });

  it("draws a Blind spell over whatever the attention state was", () => {
    const history = [
      attention(T0 + 30, "focused", "phone_use"),
      at(T0 + 40, "blind.started", { reason: "robot_stream_stalled" }),
      at(T0 + 50, "blind.ended", { reason: "robot_stream_stalled", seconds: 10 }),
    ];
    expect(buildTimeline(history, session, T0 + 70).segments).toEqual([
      { state: "focused", from: 0, to: 30 },
      { state: "phone_use", from: 30, to: 40 },
      { state: "blind", from: 40, to: 50 },
      { state: "phone_use", from: 50, to: 70 },
    ]);
  });

  it("collapses a Pause, as the session clock does", () => {
    const paused = { ...session, paused_total_s: 30 };
    const history = [
      tool(T0 + 20, "pause", { paused: true }),
      tool(T0 + 50, "resume", { resumed: true }),
      attention(T0 + 60, "focused", "away"),
    ];
    expect(buildTimeline(history, paused, T0 + 80)).toEqual({
      elapsedS: 50,
      segments: [
        { state: "focused", from: 0, to: 30 },
        { state: "away", from: 30, to: 50 },
      ],
      nudges: [],
    });
  });

  it("starts from the first known change's `was` when older history was trimmed", () => {
    const history = [attention(T0 + 50, "away", "focused")];
    expect(buildTimeline(history, session, T0 + 100).segments).toEqual([
      { state: "away", from: 0, to: 50 },
      { state: "focused", from: 50, to: 100 },
    ]);
  });

  it("marks each Nudge at its session time and ignores an earlier session's records", () => {
    const history = [
      attention(T0 - 200, "focused", "phone_use"),
      at(T0 - 150, "nudge", { episode: 1, intensity: "subtle", wakeup: null }),
      at(T0 + 45, "nudge", { episode: 1, intensity: "subtle", wakeup: null }),
    ];
    const timeline = buildTimeline(history, session, T0 + 100);
    expect(timeline.nudges).toEqual([45]);
    expect(timeline.segments).toEqual([{ state: "focused", from: 0, to: 100 }]);
  });

  it("stops at the Duration once time is up", () => {
    const up = { ...session, time_up_at: T0 + 600 };
    expect(buildTimeline([], up, T0 + 700).segments).toEqual([{ state: "focused", from: 0, to: 600 }]);
  });
});

describe("mergeHistory", () => {
  it("keeps the session's changes after the snapshot's `recent` has trimmed them", () => {
    const opened = attention(T0 + 10, "focused", "phone_use");
    const later = attention(T0 + 20, "phone_use", "focused");
    const detections = at(T0 + 21, "detections", { detect_ms: 18, detected: 10, frames: 10 });
    const first = mergeHistory([], [opened], session);
    const second = mergeHistory(first, [later, detections], session);
    expect(second).toEqual([opened, later]);
    expect(mergeHistory(second, [later], session)).toBe(second); // nothing new: the same array
  });

  it("starts over for a new session", () => {
    const old = mergeHistory([], [attention(T0 + 10, "focused", "away")], session);
    const next = { ...session, started_at: T0 + 900 };
    expect(mergeHistory(old, [], next)).toEqual([]);
  });
});

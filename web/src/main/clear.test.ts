import { describe, expect, it } from "vitest";
import type { EndedView, SessionView } from "../protocol.gen";
import type { LiveState } from "../stream/feed";
import { applyEnvelope } from "../stream/state";
import { afterClearing, clearedSessions, clearInS, endedCopy, endedSummary } from "./clear";
import { scene } from "./view";

// A 10-minute session from epoch 1000 s that the owner ended at 1400 s (6:40 in), after 2 episodes and 3 Nudges.
const session: SessionView = {
  task: "Email",
  started_at: 1_000,
  duration_s: 600,
  paused_since: null,
  paused_total_s: 0,
  time_up_at: null,
  blind: null,
  episodes: 2,
  nudges: 3,
};
const ended: EndedView = { session, ended_at: 1_400, reason: "ended_by_owner" };
const link = {
  blind_spells: 0,
  camera_fps: 30,
  detect_ms: 18,
  down: null,
  llm_latency_s: null,
  reconnects: 0,
  stream_lag_s: 0.2,
};
const afterEnd: LiveState = {
  connection: "live",
  downSinceS: null,
  synced: true,
  seq: 10,
  session: null,
  ended,
  clear_after_s: 60,
  attention: null,
  episode: null,
  agent: { phase: "idle" },
  link,
  recent: [],
  recent_max: 500,
};

const memory = (): Pick<Storage, "getItem" | "setItem"> => {
  const items = new Map<string, string>();
  return { getItem: (k) => items.get(k) ?? null, setItem: (k, v) => void items.set(k, v) };
};

describe("after a Focus Session ends", () => {
  it("shows the ended view, counting down clear_after_s on Sonny's clock", () => {
    expect(scene(afterClearing(afterEnd, 1_400, null))).toBe("ended");
    expect(clearInS(afterEnd, 1_400)).toBe(60);
    expect(clearInS(afterEnd, 1_430.5)).toBe(29.5);
  });

  it("clears itself to the resting view once clear_after_s has passed", () => {
    expect(scene(afterClearing(afterEnd, 1_459.9, null))).toBe("ended");
    const cleared = afterClearing(afterEnd, 1_460, null);
    expect(scene(cleared)).toBe("resting");
    expect([cleared.session, cleared.ended]).toEqual([null, null]);
    expect(clearInS(cleared, 1_460)).toBeNull();
  });

  it("clears at once on CLEAR NOW, and stays cleared on the next page load", () => {
    const storage = memory();
    const store = clearedSessions(storage);
    store.clear(ended);
    expect(scene(afterClearing(afterEnd, 1_401, store.cleared()))).toBe("resting");
    expect(scene(afterClearing(afterEnd, 1_401, clearedSessions(storage).cleared()))).toBe("resting");
  });

  it("clearing one session leaves the next one's end to show", () => {
    const next = { ...ended, session: { ...session, started_at: 2_000 }, ended_at: 2_300 };
    expect(scene(afterClearing({ ...afterEnd, ended: next }, 2_301, 1_000))).toBe("ended");
  });

  it("clears a session in its extend grace (time's up) the same way", () => {
    const timeUp = { ...session, time_up_at: 1_600 };
    const graced: LiveState = { ...afterEnd, session: timeUp, ended: { session: timeUp, ended_at: 1_600, reason: "completed" } };
    expect(scene(afterClearing(graced, 1_610, null))).toBe("ended");
    expect(afterClearing(graced, 1_660, null).session).toBeNull();
    expect(afterClearing(graced, 1_610, 1_000).session).toBeNull();
  });

  it("a new session clears the ended view at once", () => {
    const started = applyEnvelope(afterEnd, {
      seq: 11,
      t: 1_410,
      type: "session.started",
      data: { task: "Read", duration_min: 25 },
    });
    const shown = afterClearing({ ...afterEnd, ...started }, 1_411, null);
    expect(scene(shown)).toBe("focused");
    expect(shown.session?.task).toBe("Read");
  });

  it("sums the session up as how long it ran, its episodes and its nudges", () => {
    expect(endedSummary(ended)).toBe("6:40 · 2 episodes · 3 nudges");
    const one = { ...session, episodes: 1, nudges: 1, time_up_at: 1_600 };
    expect(endedSummary({ session: one, ended_at: 1_600, reason: "completed" })).toBe("10:00 · 1 episode · 1 nudge");
    const paused = { ...session, paused_since: 1_300, paused_total_s: 60, episodes: 0, nudges: 0 };
    expect(endedSummary({ session: paused, ended_at: 3_100, reason: "pause_timeout" })).toBe("4:00 · 0 episodes · 0 nudges");
  });

  it("says COMPLETE when time was up and ENDED for any other end", () => {
    expect(endedCopy("completed")).toEqual({ headline: "Focus Session complete", word: "Complete" });
    for (const reason of ["ended_by_owner", "ended_while_blind", "ended_during_sleep", "pause_timeout", "app_quit"] as const) {
      expect(endedCopy(reason)).toEqual({ headline: "Focus Session ended", word: "Ended" });
    }
  });

  it("still clears when the browser refuses storage", () => {
    const refusing = {
      getItem: () => {
        throw new Error("denied");
      },
      setItem: () => {
        throw new Error("denied");
      },
    };
    const store = clearedSessions(refusing);
    expect(store.cleared()).toBeNull();
    store.clear(ended);
    expect(store.cleared()).toBe(1_000);
  });
});

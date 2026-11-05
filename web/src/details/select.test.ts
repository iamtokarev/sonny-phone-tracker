import { describe, expect, it } from "vitest";
import type { Envelope } from "../protocol.gen";
import { health, recentEvents, transcript } from "./select";

let seq = 0;
const env = (t: number, type: string, data: object) => ({ seq: ++seq, t, type, data }) as unknown as Envelope;
const said = (t: number, text: string) => env(t, "line", { text, tts_start: t, tts_end: t + 2, interrupted: false });
const heard = (t: number, text: string) => env(t, "owner.spoke", { text });

describe("the transcript", () => {
  it("lists what Sonny said and what you said, newest first, timed from the session start", () => {
    const recent = [heard(1010, "Hey Sonny, how much time is left?"), said(1012, "About eight minutes.")];
    expect(transcript(recent, 1000)).toEqual([
      { seq: recent[1].seq, clock: "+00:12", who: "Sonny", text: "About eight minutes.", nudge: false },
      { seq: recent[0].seq, clock: "+00:10", who: "You", text: "Hey Sonny, how much time is left?", nudge: false },
    ]);
  });

  it("keeps only the last 6 lines and skips everything that isn't one", () => {
    const recent = [
      said(1001, "one"),
      env(1002, "detections", { detect_ms: 18, detected: 1, frames: 30 }),
      said(1003, "two"),
      heard(1004, "three"),
      env(1005, "tool_call", { name: "pause", args: {}, result: { paused: true } }),
      said(1006, "four"),
      heard(1007, "five"),
      said(1008, "six"),
      said(1009, "seven"),
    ];
    expect(transcript(recent, 1000).map((l) => l.text)).toEqual(["seven", "six", "five", "four", "three", "two"]);
  });

  it("shows a Nudge as its Distraction Episode's kind and intensity", () => {
    const recent = [
      env(1030, "episode_opened", { number: 1, kind: "phone_use" }),
      env(1040, "nudge", { episode: 1, intensity: "subtle", wakeup: null }),
      env(1050, "attention", { state: "away", was: "phone_use" }),
      env(1060, "nudge", { episode: 1, intensity: "medium", wakeup: null }),
    ];
    expect(transcript(recent, 1000)).toMatchObject([
      { clock: "+01:00", who: "Nudge", text: "Away · medium", nudge: true },
      { clock: "+00:40", who: "Nudge", text: "Phone use · subtle", nudge: true },
    ]);
  });

  it("names only the intensity when the episode it belongs to is out of sight", () => {
    const recent = [env(1040, "nudge", { episode: 4, intensity: "strong", wakeup: null })];
    expect(transcript(recent, 1000)[0].text).toBe("Strong");
  });

  it("times lines before the session started with a minus, and by the wall clock without a session", () => {
    expect(transcript([said(986, "Ready when you are.")], 1000)[0].clock).toBe("-00:14");
    const at = new Date(2026, 10, 5, 9, 4, 7).getTime() / 1000;
    expect(transcript([said(at, "Hi.")], null)[0].clock).toBe("09:04:07");
  });
});

describe("the health", () => {
  const link = { blind_spells: 0, camera_fps: 29.43, detect_ms: 17.46, down: null, llm_latency_s: 1.214, reconnects: 1, stream_lag_s: 0.1684 };

  it("reads the link, fps, detect ms, lag, LLM s and reconnects off the latest health", () => {
    expect(health(link)).toEqual([
      { label: "Link", value: "OK" },
      { label: "FPS", value: "29.4" },
      { label: "Detect ms", value: "17.5" },
      { label: "Lag ms", value: "168" },
      { label: "LLM s", value: "1.21" },
      { label: "Reconnects", value: "1" },
    ]);
  });

  it("shows a lost link, and a dash for what hasn't been measured", () => {
    const down = { ...link, down: "daemon unreachable", camera_fps: null, detect_ms: null, llm_latency_s: null, stream_lag_s: null };
    expect(health(down).map((r) => r.value)).toEqual(["Lost", "—", "—", "—", "—", "1"]);
  });
});

describe("the recent events", () => {
  it("lists the latest 7 by type and time, newest first", () => {
    const recent = Array.from({ length: 9 }, (_, i) => env(1000 + i, i % 2 ? "wake" : "hook_set", {}));
    const events = recentEvents(recent, 1000);
    expect(events).toHaveLength(7);
    expect(events[0]).toEqual({ seq: recent[8].seq, clock: "+00:08", type: "hook_set" });
    expect(events.at(-1)).toEqual({ seq: recent[2].seq, clock: "+00:02", type: "hook_set" });
  });

  it("leaves out the detector's once-a-second record, which the health already reads", () => {
    const recent = [env(1001, "attention", { state: "away", was: "focused" }), env(1002, "detections", { detect_ms: 18 })];
    expect(recentEvents(recent, 1000).map((e) => e.type)).toEqual(["attention"]);
  });
});

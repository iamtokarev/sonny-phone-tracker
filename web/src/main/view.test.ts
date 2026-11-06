import { describe, expect, it } from "vitest";
import type { Envelope, SessionView } from "../protocol.gen";
import type { LiveState } from "../stream/feed";
import { caption, scene, statusLine } from "./view";

const session: SessionView = {
  task: "Email",
  started_at: 1_000,
  duration_s: 600,
  paused_since: null,
  paused_total_s: 0,
  time_up_at: null,
  blind: null,
  episodes: 0,
  nudges: 0,
};
const link = {
  blind_spells: 0,
  camera_fps: 30,
  detect_ms: 18,
  down: null,
  llm_latency_s: null,
  reconnects: 0,
  stream_lag_s: 0.2,
};
const live: LiveState = {
  connection: "live",
  downSinceS: null,
  synced: true,
  seq: 10,
  session,
  ended: null,
  clear_after_s: 60,
  attention: "focused",
  episode: null,
  agent: { phase: "idle" },
  link,
  recent: [],
  recent_max: 500,
};

describe("scene", () => {
  it("is Resting with no session, Ended once time is up, else the Attention State", () => {
    expect(scene({ ...live, session: null, attention: null })).toBe("resting");
    expect(scene({ ...live, session: { ...session, time_up_at: 1_600 }, attention: null })).toBe("ended");
    expect(scene({ ...live, attention: "phone_use" })).toBe("phone_use");
    expect(scene({ ...live, attention: "away" })).toBe("away");
  });

  it("is Blind during a Blind spell, whatever the last attention was", () => {
    expect(scene({ ...live, attention: "phone_use", session: { ...session, blind: "robot_stream_stalled" } })).toBe("blind");
  });
});

describe("statusLine", () => {
  it("reads ALL OK in a session, STANDBY outside one, and blinks LINK LOST while Blind", () => {
    expect(statusLine(live, 1_100)).toEqual({ text: "All OK", problem: false, blink: false });
    expect(statusLine({ ...live, session: null }, 1_100)).toEqual({ text: "Standby", problem: false, blink: false });
    expect(statusLine({ ...live, session: { ...session, blind: "robot_stream_stalled" } }, 1_100)).toEqual({
      text: "Link lost",
      problem: true,
      blink: true,
    });
  });

  it("shows a lost link outside a session without blinking, and the page's own connection first", () => {
    expect(statusLine({ ...live, session: null, link: { ...link, down: "daemon_unreachable" } }, 1_100)).toEqual({
      text: "Link lost",
      problem: true,
      blink: false,
    });
    expect(statusLine({ ...live, connection: "down", downSinceS: 1_000 }, 1_001).text).toBe("Connecting");
  });

  it("reads CONNECTING for the first seconds without /rpc, then SONNY OFFLINE as a problem", () => {
    const lost: LiveState = { ...live, connection: "down", downSinceS: 1_000 };
    expect(statusLine(lost, 1_004.9)).toEqual({ text: "Connecting", problem: false, blink: false });
    expect(statusLine(lost, 1_005)).toEqual({ text: "Sonny offline", problem: true, blink: false });
    expect(statusLine({ ...lost, connection: "connecting" }, 1_060)).toEqual({ text: "Sonny offline", problem: true, blink: false });
    expect(statusLine({ ...lost, connection: "syncing" }, 1_002).text).toBe("Connecting");
  });
});

describe("caption", () => {
  const line = (seq: number, t: number, text: string): Envelope =>
    ({ seq, t, type: "line", data: { text, tts_start: t - 3, tts_end: t, interrupted: false } }) as Envelope;
  const phase = (seq: number, t: number, p: string): Envelope => ({ seq, t, type: "agent.phase", data: { phase: p } }) as Envelope;

  it("is Sonny's latest line, not bold once said", () => {
    const recent = [line(1, 1_010, "Ten minutes on email."), line(2, 1_030, "Keep at it!")];
    expect(caption({ ...live, recent })).toEqual({ text: "“Keep at it!”", live: false });
  });

  it("says nothing yet before the first line", () => {
    expect(caption(live)).toEqual({ text: "Nothing said yet.", live: false });
  });

  it("is a bold ellipsis while a new line is being spoken (its text arrives when it ends)", () => {
    const recent = [line(1, 1_010, "Ten minutes on email."), phase(2, 1_020, "speaking")];
    expect(caption({ ...live, recent, agent: { phase: "speaking" } })).toEqual({ text: "…", live: true });
  });
});

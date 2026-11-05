import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Envelope, Snapshot } from "../protocol.gen";
import { connectFeed, type Feed, type FeedSocket } from "./feed";

// A stand-in for the browser's WebSocket to Sonny's /rpc.
class FakeSocket implements FeedSocket {
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: unknown }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  sent: { jsonrpc: string; id: number; method: string }[] = [];
  closed = false;

  send(data: string) {
    this.sent.push(JSON.parse(data));
  }
  close() {
    this.closed = true;
  }

  // What Sonny does.
  open() {
    this.onopen?.();
  }
  snapshotRequests() {
    return this.sent.filter((m) => m.method === "sonny.snapshot");
  }
  answerSnapshot(result: Snapshot) {
    const request = this.snapshotRequests().at(-1)!;
    this.onmessage?.({ data: JSON.stringify({ jsonrpc: "2.0", id: request.id, result }) });
  }
  event(envelope: Envelope) {
    this.onmessage?.({ data: JSON.stringify({ jsonrpc: "2.0", method: "sonny.event", params: envelope }) });
  }
  drop() {
    this.onclose?.();
  }
}

const health = { blind_spells: 0, camera_fps: 30, detect_ms: 40, down: null, llm_latency_s: null, reconnects: 0, stream_lag_s: 0.1 };

function snapshot(seq: number, over: Partial<Snapshot> = {}): Snapshot {
  return {
    seq,
    server_now: 2000,
    session: {
      task: "Email",
      started_at: 1900,
      duration_s: 600,
      paused_since: null,
      paused_total_s: 0,
      time_up_at: null,
      blind: null,
    },
    attention: "focused",
    episode: null,
    agent: { phase: "idle" },
    link: health,
    recent: [],
    ...over,
  };
}

const attention = (seq: number, state: "focused" | "phone_use" | "away", was = "focused" as const): Envelope => ({
  seq,
  t: 2000 + seq,
  type: "attention",
  data: { state, was },
});
const phase = (seq: number, p: "idle" | "listening" | "thinking" | "speaking"): Envelope => ({
  seq,
  t: 2000 + seq,
  type: "agent.phase",
  data: { phase: p },
});

let sockets: FakeSocket[];
let feed: Feed;
const socket = () => sockets.at(-1)!;

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(2000_000);
  sockets = [];
  feed = connectFeed("ws://127.0.0.1:8042/rpc", {
    open: () => {
      const s = new FakeSocket();
      sockets.push(s);
      return s;
    },
  });
});

afterEach(() => {
  feed.close();
  vi.useRealTimers();
});

describe("connecting to /rpc", () => {
  it("asks for a snapshot once the socket opens, and applies it", () => {
    expect(feed.getState().connection).toBe("connecting");
    socket().open();
    expect(socket().snapshotRequests()).toHaveLength(1);
    expect(feed.getState().connection).toBe("syncing");

    socket().answerSnapshot(snapshot(7, { attention: "away" }));

    const state = feed.getState();
    expect(state.connection).toBe("live");
    expect(state.seq).toBe(7);
    expect(state.attention).toBe("away");
    expect(state.session?.task).toBe("Email");
  });

  it("buffers events that arrive before the snapshot and replays only the newer ones", () => {
    socket().open();
    socket().event(attention(6, "phone_use")); // already in the snapshot
    socket().event(attention(8, "phone_use"));
    socket().event(phase(9, "listening"));
    socket().answerSnapshot(snapshot(7, { attention: "away" }));

    const state = feed.getState();
    expect(state.seq).toBe(9);
    expect(state.attention).toBe("phone_use");
    expect(state.agent.phase).toBe("listening");
    expect(state.recent.map((e) => e.seq)).toEqual([8, 9]);
  });

  it("tells subscribers when the state changes", () => {
    const seen: number[] = [];
    feed.subscribe(() => seen.push(feed.getState().seq));
    socket().open();
    socket().answerSnapshot(snapshot(3));
    socket().event(phase(4, "thinking"));
    expect(seen.at(-1)).toBe(4);
  });
});

describe("while live", () => {
  beforeEach(() => {
    socket().open();
    socket().answerSnapshot(snapshot(10));
  });

  it("applies each next event", () => {
    socket().event(attention(11, "phone_use"));
    expect(feed.getState().attention).toBe("phone_use");
    expect(feed.getState().seq).toBe(11);
  });

  it("drops duplicate and old events", () => {
    socket().event(attention(11, "phone_use"));
    socket().event(attention(11, "away"));
    socket().event(attention(9, "away"));
    expect(feed.getState().attention).toBe("phone_use");
    expect(feed.getState().recent.map((e) => e.seq)).toEqual([11]);
  });

  it("takes a new snapshot on a seq gap, buffering meanwhile", () => {
    socket().event(attention(13, "phone_use")); // 11 and 12 went missing
    expect(feed.getState().seq).toBe(10);
    expect(feed.getState().connection).toBe("syncing");
    expect(socket().snapshotRequests()).toHaveLength(2);

    socket().event(phase(14, "speaking"));
    socket().answerSnapshot(snapshot(13, { attention: "phone_use" }));
    expect(feed.getState().seq).toBe(14);
    expect(feed.getState().connection).toBe("live");
    expect(feed.getState().agent.phase).toBe("speaking");
  });

  it("keeps the latest health as the link, out of the recent events", () => {
    socket().event({ seq: 11, t: 2011, type: "health", data: { ...health, down: "daemon unreachable" } });
    expect(feed.getState().link.down).toBe("daemon unreachable");
    expect(feed.getState().recent).toEqual([]);
  });
});

describe("following the state the snapshot carries", () => {
  beforeEach(() => {
    socket().open();
    socket().answerSnapshot(snapshot(0, { session: null, attention: null }));
  });

  const at = (seq: number, t: number, type: string, data: object) =>
    socket().event({ seq, t, type, data } as unknown as Envelope);

  it("opens and ends a Focus Session", () => {
    at(1, 3000, "session.started", { task: "Write", duration_min: 25 });
    expect(feed.getState().session).toMatchObject({ task: "Write", started_at: 3000, duration_s: 1500 });
    expect(feed.getState().attention).toBe("focused");
    at(2, 3100, "session.ended", { reason: "ended_by_owner" });
    expect(feed.getState().session).toBeNull();
    expect(feed.getState().attention).toBeNull();
  });

  it("keeps the session through the extend grace when time is up", () => {
    at(1, 3000, "session.started", { task: "Write", duration_min: 25 });
    at(2, 4500, "session.ended", { reason: "completed" });
    expect(feed.getState().session?.time_up_at).toBe(4500);
    expect(feed.getState().attention).toBeNull();
  });

  it("follows Pause and resume", () => {
    at(1, 3000, "session.started", { task: "Write", duration_min: 25 });
    at(2, 3100, "tool_call", { name: "pause", args: {}, result: { paused: true } });
    expect(feed.getState().session?.paused_since).toBe(3100);
    at(3, 3220, "tool_call", { name: "resume", args: {}, result: { resumed: true } });
    expect(feed.getState().session).toMatchObject({ paused_since: null, paused_total_s: 120 });
  });

  it("follows an extend, as the session clock does", () => {
    at(1, 3000, "session.started", { task: "Write", duration_min: 25 });
    at(2, 3100, "tool_call", { name: "extend", args: {}, result: { extended_by_min: 5 } });
    expect(feed.getState().session?.duration_s).toBe(1800);
    // Time's up at 4800, extended at 4900 after the grace began: elapsed stays at the old Duration.
    at(3, 4800, "session.ended", { reason: "completed" });
    at(4, 4900, "tool_call", { name: "extend", args: {}, result: { extended_by_min: 10, continued: true } });
    expect(feed.getState().session).toMatchObject({ time_up_at: null, duration_s: 2400, paused_total_s: 100 });
  });

  it("follows the Distraction Episode and its Nudges", () => {
    at(1, 3000, "session.started", { task: "Write", duration_min: 25 });
    at(2, 3050, "episode_opened", { number: 1, kind: "phone_use" });
    at(3, 3060, "nudge", { episode: 1, intensity: "gentle", wakeup: null });
    expect(feed.getState().episode).toEqual({ number: 1, kind: "phone_use", started_at: 3050, nudges: ["gentle"] });
    at(4, 3070, "attention", { state: "away", was: "phone_use" });
    expect(feed.getState().episode?.kind).toBe("away");
    at(5, 3080, "episode_closed", { number: 1, kind: "away" });
    expect(feed.getState().episode).toBeNull();
  });

  it("follows Blind", () => {
    at(1, 3000, "session.started", { task: "Write", duration_min: 25 });
    at(2, 3010, "blind.started", { reason: "camera" });
    expect(feed.getState().session?.blind).toBe("camera");
    expect(feed.getState().link.blind_spells).toBe(1);
    at(3, 3020, "blind.ended", { seconds: 10 });
    expect(feed.getState().session?.blind).toBeNull();
  });
});

describe("when the socket closes", () => {
  it("reconnects, backing off from 0.5 s to 5 s", () => {
    const waits: number[] = [];
    for (let i = 0; i < 6; i++) {
      socket().drop();
      expect(feed.getState().connection).toBe("down");
      const before = sockets.length;
      let waited = 0;
      while (sockets.length === before) {
        vi.advanceTimersByTime(100);
        waited += 100;
      }
      waits.push(waited);
    }
    expect(waits).toEqual([500, 1000, 2000, 4000, 5000, 5000]);
  });

  it("starts the backoff over once a snapshot lands", () => {
    socket().drop();
    vi.advanceTimersByTime(500);
    socket().drop();
    vi.advanceTimersByTime(1000);
    socket().open();
    socket().answerSnapshot(snapshot(1));
    socket().drop();
    const before = sockets.length;
    vi.advanceTimersByTime(500);
    expect(sockets.length).toBe(before + 1);
  });

  it("records since when the page has been without a live feed, on Sonny's clock", () => {
    expect(feed.getState().downSinceS).toBeCloseTo(2000, 1); // not live yet since the page opened
    socket().open();
    socket().answerSnapshot(snapshot(1, { server_now: 2010 })); // Sonny's clock is 10 s ahead
    expect(feed.getState().downSinceS).toBeNull();
    vi.advanceTimersByTime(3000);
    socket().drop();
    expect(feed.getState().downSinceS).toBeCloseTo(2013, 1);
    vi.advanceTimersByTime(600);
    socket().drop(); // a failed retry keeps the first moment it went down
    expect(feed.getState().downSinceS).toBeCloseTo(2013, 1);
  });

  it("stops for good when the feed is closed", () => {
    feed.close();
    expect(socket().closed).toBe(true);
    socket().drop();
    vi.advanceTimersByTime(10_000);
    expect(sockets).toHaveLength(1);
  });
});

describe("the clock", () => {
  it("learns Sonny's clock from the snapshot so the time remaining ticks on Sonny's time", () => {
    vi.setSystemTime(1990_000); // the page's clock is 10 s behind Sonny's
    socket().open();
    socket().answerSnapshot(snapshot(1, { server_now: 2000 }));
    expect(feed.nowS()).toBeCloseTo(2000, 1);
    vi.advanceTimersByTime(1500);
    expect(feed.nowS()).toBeCloseTo(2001.5, 1);
  });
});

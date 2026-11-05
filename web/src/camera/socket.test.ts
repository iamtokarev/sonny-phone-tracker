import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { connectCamera, type CameraClient, type CameraSocket } from "./socket";

// A stand-in for the browser's WebSocket to Sonny's /ws/camera.
class FakeSocket implements CameraSocket {
  binaryType = "blob";
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: unknown }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  sent: unknown[] = [];
  constructor(readonly url: string) {}
  send(data: string) {
    this.sent.push(JSON.parse(data));
  }
  close() {}
}

let sockets: FakeSocket[];
let client: CameraClient | null;
const open = (url: string) => {
  const s = new FakeSocket(url);
  sockets.push(s);
  return s;
};
const base = "ws://127.0.0.1:8042/ws/camera";

/** One /ws/camera message: `u32 header_len | JSON header | JPEG` (the JPEG bytes need not decode here). */
function frameMessage(seq: number): ArrayBuffer {
  const header = { seq, t: 1, w: 4, h: 4, boxes: [], attention: null, watching: false };
  const json = new TextEncoder().encode(JSON.stringify(header));
  const out = new Uint8Array(4 + json.length + 2);
  new DataView(out.buffer).setUint32(0, json.length, true);
  out.set(json, 4);
  return out.buffer;
}

beforeEach(() => {
  vi.useFakeTimers();
  sockets = [];
  client = null;
});
afterEach(() => {
  client?.close();
  vi.useRealTimers();
});

describe("the camera socket", () => {
  it("connects with its feed already off when the page starts paused, so Sonny encodes nothing for it", () => {
    client = connectCamera(base, () => {}, { paused: true, open });
    expect(sockets[0].url).toBe(`${base}?paused=1`);
    sockets[0].onopen?.();
    expect(sockets[0].sent).toEqual([]);
    client.setPaused(false);
    expect(sockets[0].sent).toEqual([{ type: "feed.on" }]);
  });

  it("turns the feed off and on with feed.off and feed.on, never the session's pause", () => {
    client = connectCamera(base, () => {}, { open });
    expect(sockets[0].url).toBe(base);
    sockets[0].onopen?.();
    client.setPaused(true);
    client.setPaused(false);
    expect(sockets[0].sent).toEqual([{ type: "feed.off" }, { type: "feed.on" }]);
  });

  it("reconnects with its feed off after the feed was turned off", () => {
    client = connectCamera(base, () => {}, { open });
    sockets[0].onopen?.();
    client.setPaused(true);
    sockets[0].onclose?.();
    vi.advanceTimersByTime(500);
    expect(sockets[1].url).toBe(`${base}?paused=1`);
  });

  it("starts the backoff over on the first frame, not when a socket merely opens", () => {
    client = connectCamera(base, () => {}, { open });
    const retryAfter = () => {
      sockets.at(-1)!.onclose?.();
      const before = sockets.length;
      let waited = 0;
      while (sockets.length === before) {
        vi.advanceTimersByTime(100);
        waited += 100;
      }
      return waited;
    };
    expect(retryAfter()).toBe(500);
    sockets.at(-1)!.onopen?.();
    expect(retryAfter()).toBe(1000); // opened, but no frame came
    sockets.at(-1)!.onopen?.();
    sockets.at(-1)!.onmessage?.({ data: frameMessage(1) });
    expect(retryAfter()).toBe(500);
  });

  it("catches up on a choice made before the socket opened", () => {
    client = connectCamera(base, () => {}, { open });
    client.setPaused(true); // still connecting: nothing to send yet
    sockets[0].onopen?.();
    expect(sockets[0].sent).toEqual([{ type: "feed.off" }]);
  });
});

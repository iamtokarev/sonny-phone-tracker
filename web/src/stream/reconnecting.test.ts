import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { reconnecting, sameOriginUrl, type BasicSocket, type Reconnecting } from "./reconnecting";

class FakeSocket implements BasicSocket {
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: unknown }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;
  constructor(readonly url: string) {}
  send() {}
  close() {
    this.closed = true;
  }
}

let sockets: FakeSocket[];
let messages: unknown[];
let downs: number;
let conn: Reconnecting<FakeSocket>;
const socket = () => sockets.at(-1)!;

beforeEach(() => {
  vi.useFakeTimers();
  sockets = [];
  messages = [];
  downs = 0;
  let n = 0;
  conn = reconnecting<FakeSocket>({
    url: () => `ws://sonny/x?n=${++n}`,
    open: (url) => {
      const s = new FakeSocket(url);
      sockets.push(s);
      return s;
    },
    onMessage: (data) => messages.push(data),
    onDown: () => downs++,
  });
});

afterEach(() => {
  conn.close();
  vi.useRealTimers();
});

/** How long the next retry takes after the current socket drops, in ms. */
function nextWait(): number {
  socket().onclose?.();
  const before = sockets.length;
  let waited = 0;
  while (sockets.length === before) {
    vi.advanceTimersByTime(100);
    waited += 100;
  }
  return waited;
}

describe("a reconnecting socket", () => {
  it("reconnects, backing off from 0.5 s to 5 s, and builds each URL afresh", () => {
    const waits = Array.from({ length: 6 }, nextWait);
    expect(waits).toEqual([500, 1000, 2000, 4000, 5000, 5000]);
    expect(downs).toBe(6);
    expect(socket().url).toBe("ws://sonny/x?n=7");
  });

  it("keeps backing off when sockets open but the stream never gets healthy", () => {
    socket().onopen?.();
    nextWait();
    socket().onopen?.();
    expect(nextWait()).toBe(1000);
  });

  it("starts the backoff over once the stream is healthy", () => {
    nextWait();
    nextWait();
    conn.healthy();
    expect(nextWait()).toBe(500);
  });

  it("ignores a replaced socket's late events", () => {
    const old = socket();
    nextWait();
    old.onmessage?.({ data: "stale" });
    old.onclose?.();
    socket().onmessage?.({ data: "fresh" });
    expect(messages).toEqual(["fresh"]);
    expect(sockets).toHaveLength(2);
  });

  it("is open only between its socket's open and close", () => {
    expect(conn.openSocket()).toBeNull();
    socket().onopen?.();
    expect(conn.openSocket()).toBe(socket());
    socket().onclose?.();
    expect(conn.openSocket()).toBeNull();
  });

  it("stops for good when closed", () => {
    conn.close();
    expect(socket().closed).toBe(true);
    socket().onclose?.();
    socket().onmessage?.({ data: "late" });
    vi.advanceTimersByTime(10_000);
    expect(sockets).toHaveLength(1);
    expect(messages).toEqual([]);
  });
});

describe("sameOriginUrl", () => {
  it("uses ws on http and wss on https, on the page's own host", () => {
    expect(sameOriginUrl("/rpc", { protocol: "http:", host: "127.0.0.1:8042" })).toBe("ws://127.0.0.1:8042/rpc");
    expect(sameOriginUrl("/ws/camera", { protocol: "https:", host: "x:1" })).toBe("wss://x:1/ws/camera");
  });
});

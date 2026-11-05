// The one owner of the /rpc socket (spec §4, client side).
//
// Sonny pushes every envelope to each open socket as a `sonny.event` notification, so the page is
// subscribed from the moment the socket opens. It buffers those, asks for `sonny.snapshot`, applies it, then
// replays the buffered envelopes with `seq > snapshot.seq`. Live envelopes must come in `seq` order: an old
// or repeated one is dropped, a gap means one was lost, so the page snapshots again. When the socket closes
// it reconnects (reconnecting.ts: 0.5 s doubling up to 5 s, starting over once a snapshot lands) and starts
// over from a fresh snapshot.
import type { Envelope, Snapshot } from "../protocol.gen";
import { reconnecting, sameOriginUrl, type Backoff, type BasicSocket } from "./reconnecting";
import { applyEnvelope, fromSnapshot, type FeedView } from "./state";

/** connecting: socket not open yet; syncing: waiting for a snapshot; live: following; down: waiting to retry. */
export type Connection = "connecting" | "syncing" | "live" | "down";

export type FeedSocket = BasicSocket;

export interface FeedOptions {
  open?: (url: string) => FeedSocket;
  backoffS?: Backoff;
}

export interface Feed {
  getState(): Readonly<LiveState>;
  subscribe(listener: () => void): () => void;
  /** Epoch seconds on Sonny's clock (learnt from the last snapshot), for ticking the time remaining locally. */
  nowS(): number;
  close(): void;
}

export interface LiveState extends FeedView {
  connection: Connection;
  /** Epoch seconds on Sonny's clock since which the feed has not been live (since the page opened, at first);
   *  null while live. The status line reads it to tell a brief reconnect from Sonny being gone. */
  downSinceS: number | null;
  /** False until the first snapshot lands; the rest of the fields are empty until then. */
  synced: boolean;
}

const EMPTY: LiveState = {
  connection: "connecting",
  downSinceS: null,
  synced: false,
  seq: 0,
  session: null,
  attention: null,
  episode: null,
  agent: { phase: "idle" },
  link: {
    blind_spells: 0,
    camera_fps: null,
    detect_ms: null,
    down: null,
    llm_latency_s: null,
    reconnects: 0,
    stream_lag_s: null,
  },
  recent: [],
};

/** The /rpc URL on the page's own origin (Sonny in production, Vite's proxy in dev). */
export function rpcUrl(location: Pick<Location, "protocol" | "host"> = window.location): string {
  return sameOriginUrl("/rpc", location);
}

export function connectFeed(url: string, options: FeedOptions = {}): Feed {
  let offsetS = 0; // Sonny's clock minus the page's
  let state: LiveState = { ...EMPTY, downSinceS: Date.now() / 1000 };
  let buffer: Envelope[] = [];
  let pendingId: number | null = null;
  let nextId = 1;
  const listeners = new Set<() => void>();

  const nowS = () => Date.now() / 1000 + offsetS;

  const set = (next: LiveState) => {
    const downSinceS = next.connection === "live" ? null : (state.downSinceS ?? nowS());
    state = next.downSinceS === downSinceS ? next : { ...next, downSinceS };
    for (const listener of listeners) listener();
  };

  const requestSnapshot = (socket: FeedSocket | null) => {
    pendingId = nextId++;
    socket?.send(JSON.stringify({ jsonrpc: "2.0", id: pendingId, method: "sonny.snapshot", params: {} }));
    if (state.connection !== "syncing") set({ ...state, connection: "syncing" });
  };

  // Apply envelopes in seq order from `view`; on a gap, keep the rest buffered and snapshot again.
  const follow = (view: LiveState, envelopes: Envelope[]): void => {
    const sorted = [...envelopes].sort((a, b) => a.seq - b.seq);
    for (let i = 0; i < sorted.length; i++) {
      const e = sorted[i];
      if (e.seq <= view.seq) continue; // already seen
      if (e.seq !== view.seq + 1) {
        set(view);
        buffer = sorted.slice(i);
        requestSnapshot(conn.openSocket());
        return;
      }
      view = { ...applyEnvelope(view, e), connection: view.connection, downSinceS: view.downSinceS, synced: true };
    }
    buffer = [];
    set(view);
  };

  const onSnapshot = (snapshot: Snapshot) => {
    pendingId = null;
    offsetS = snapshot.server_now - Date.now() / 1000;
    conn.healthy(); // the next drop starts the backoff over
    follow({ ...fromSnapshot(snapshot), connection: "live", downSinceS: null, synced: true }, buffer);
  };

  const onMessage = (raw: unknown, socket: FeedSocket) => {
    let message: { id?: number; method?: string; params?: unknown; result?: unknown; error?: unknown };
    try {
      message = JSON.parse(String(raw));
    } catch {
      return;
    }
    if (message.method === "sonny.event") {
      const envelope = message.params as Envelope;
      if (state.connection === "live") follow(state, [envelope]);
      else buffer.push(envelope);
    } else if (message.id !== undefined && message.id === pendingId) {
      if (message.error !== undefined) socket.close(); // start over on a fresh socket
      else onSnapshot(message.result as Snapshot);
    }
  };

  const conn = reconnecting<FeedSocket>({
    url: () => url,
    open: options.open,
    backoffS: options.backoffS,
    onConnect: () => {
      buffer = [];
      pendingId = null;
      if (state.connection !== "connecting") set({ ...state, connection: "connecting" });
    },
    onOpen: requestSnapshot,
    onMessage,
    onDown: () => set({ ...state, connection: "down" }),
  });

  return {
    getState: () => state,
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    nowS,
    close() {
      conn.close();
      listeners.clear();
    },
  };
}

// One WebSocket to Sonny that comes back when it drops, shared by the /rpc feed and the /ws/camera client.
//
// When the socket closes it opens a new one after 0.5 s, doubling up to 5 s. The backoff starts over only
// when the caller says the stream is healthy (`healthy()`: a snapshot on /rpc, the first frame on the camera),
// not merely when a socket opens, so a Sonny that accepts and then fails keeps backing off. Handlers only ever
// see their own socket: events from a socket that has been replaced or closed are dropped.

/** The part of the browser's WebSocket the clients use (tests pass a fake). */
export interface BasicSocket {
  onopen: (() => void) | null;
  onmessage: ((event: { data: unknown }) => void) | null;
  onclose: (() => void) | null;
  onerror: (() => void) | null;
  send(data: string): void;
  close(): void;
}

export interface Backoff {
  first: number;
  max: number;
}

export const DEFAULT_BACKOFF_S: Backoff = { first: 0.5, max: 5 };

export interface ReconnectingOptions<S extends BasicSocket> {
  /** The URL for each new socket (read on every connect, so it can carry the page's current choices). */
  url: () => string;
  open?: (url: string) => S;
  backoffS?: Backoff;
  /** A new socket was made (not open yet). */
  onConnect?: (socket: S) => void;
  onOpen?: (socket: S) => void;
  onMessage: (data: unknown, socket: S) => void;
  /** The socket closed; a retry is scheduled. */
  onDown?: () => void;
}

export interface Reconnecting<S extends BasicSocket> {
  /** The current socket once it is open, else null. */
  openSocket(): S | null;
  /** The stream is healthy: the next drop starts the backoff over. */
  healthy(): void;
  /** Close for good: no more retries or handler calls. */
  close(): void;
}

/** A ws:// (or wss://) URL for `path` on the page's own origin (Sonny in production, Vite's proxy in dev). */
export function sameOriginUrl(path: string, location: Pick<Location, "protocol" | "host"> = window.location): string {
  return `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}${path}`;
}

export function reconnecting<S extends BasicSocket>(options: ReconnectingOptions<S>): Reconnecting<S> {
  const open = options.open ?? ((u: string) => new WebSocket(u) as unknown as S);
  const { first, max } = options.backoffS ?? DEFAULT_BACKOFF_S;
  let socket: S | null = null;
  let isOpen = false;
  let delayS = first;
  let retry: ReturnType<typeof setTimeout> | null = null;
  let closed = false;

  const connect = () => {
    retry = null;
    isOpen = false;
    const s = open(options.url());
    socket = s;
    s.onopen = () => {
      if (socket !== s || closed) return;
      isOpen = true;
      options.onOpen?.(s);
    };
    s.onmessage = (event) => {
      if (socket === s && !closed) options.onMessage(event.data, s);
    };
    s.onerror = () => {}; // a close always follows
    s.onclose = () => {
      if (socket !== s || closed) return;
      socket = null;
      isOpen = false;
      retry = setTimeout(connect, delayS * 1000);
      delayS = Math.min(max, delayS * 2);
      options.onDown?.();
    };
    options.onConnect?.(s);
  };

  connect();

  return {
    openSocket: () => (isOpen ? socket : null),
    healthy() {
      delayS = first;
    },
    close() {
      closed = true;
      if (retry !== null) clearTimeout(retry);
      socket?.close();
      socket = null;
      isOpen = false;
    },
  };
}

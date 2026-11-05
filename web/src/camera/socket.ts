// The page's /ws/camera client (spec §5). Each binary message is one frame; the newest wins (Sonny already
// skips frames for a slow tab, and so does the page: a frame still decoding is dropped for the next one).
// PAUSE FEED sends `{"type": "feed.off"}` so Sonny encodes and sends nothing more to this tab until `feed.on`;
// the socket stays open. A page whose feed is off connects as `/ws/camera?paused=1`, so Sonny starts it off and
// no frame is encoded before the page could say so. When the socket closes the client reconnects after 0.5 s,
// doubling up to 5 s.
import { parseCameraMessage, type CameraControl, type CameraHeader } from "./message";

export interface CameraFrame {
  header: CameraHeader;
  image: ImageBitmap;
}

export interface CameraClient {
  setPaused(paused: boolean): void;
  close(): void;
}

/** The part of the browser's WebSocket the client uses (tests pass a fake). */
export interface CameraSocket {
  binaryType: string;
  onopen: (() => void) | null;
  onmessage: ((event: { data: unknown }) => void) | null;
  onclose: (() => void) | null;
  onerror: (() => void) | null;
  send(data: string): void;
  close(): void;
}

export interface CameraOptions {
  /** Start with the feed off (the owner's remembered PAUSE FEED). */
  paused?: boolean;
  open?: (url: string) => CameraSocket;
  backoffS?: { first: number; max: number };
}

export function cameraUrl(location: Pick<Location, "protocol" | "host"> = window.location): string {
  return `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/camera`;
}

export function connectCamera(url: string, onFrame: (frame: CameraFrame) => void, options: CameraOptions = {}): CameraClient {
  const open = options.open ?? ((u: string) => new WebSocket(u) as unknown as CameraSocket);
  const backoffS = options.backoffS ?? { first: 0.5, max: 5 };
  let socket: CameraSocket | null = null;
  let isOpen = false;
  let paused = options.paused ?? false;
  let sonnyPaused = paused; // what Sonny was last told (or connected with)
  let closed = false;
  let delayS = backoffS.first;
  let retry: ReturnType<typeof setTimeout> | null = null;
  let decoding = false;
  let newestSeq = 0;

  const sync = () => {
    if (socket === null || !isOpen || sonnyPaused === paused) return;
    const control: CameraControl = { type: paused ? "feed.off" : "feed.on" };
    socket.send(JSON.stringify(control));
    sonnyPaused = paused;
  };

  const onMessage = async (data: unknown) => {
    if (!(data instanceof ArrayBuffer) || paused || decoding) return;
    const message = parseCameraMessage(data);
    if (message === null || message.header.seq <= newestSeq) return;
    decoding = true;
    try {
      const image = await createImageBitmap(new Blob([message.jpeg as Uint8Array<ArrayBuffer>], { type: "image/jpeg" }));
      newestSeq = message.header.seq;
      if (closed || paused) image.close();
      else onFrame({ header: message.header, image });
    } catch {
      // a broken JPEG: wait for the next frame
    } finally {
      decoding = false;
    }
  };

  const connect = () => {
    retry = null;
    newestSeq = 0; // Sonny counts from 1 again after a restart
    isOpen = false;
    sonnyPaused = paused;
    const s = open(paused ? `${url}?paused=1` : url);
    s.binaryType = "arraybuffer";
    socket = s;
    s.onopen = () => {
      if (socket !== s) return;
      delayS = backoffS.first;
      isOpen = true;
      sync(); // a choice made while connecting
    };
    s.onmessage = (event) => {
      if (socket === s) void onMessage(event.data);
    };
    s.onerror = () => {}; // a close always follows
    s.onclose = () => {
      if (socket !== s || closed) return;
      socket = null;
      isOpen = false;
      retry = setTimeout(connect, delayS * 1000);
      delayS = Math.min(backoffS.max, delayS * 2);
    };
  };

  connect();

  return {
    setPaused(next) {
      paused = next;
      sync();
    },
    close() {
      closed = true;
      if (retry !== null) clearTimeout(retry);
      socket?.close();
      socket = null;
    },
  };
}

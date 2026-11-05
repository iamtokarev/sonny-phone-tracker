// The page's /ws/camera client (spec §5). Each binary message is one frame; the newest wins (Sonny already
// skips frames for a slow tab, and so does the page: a frame still decoding is dropped for the next one).
// PAUSE FEED sends `{"type": "feed.off"}` so Sonny encodes and sends nothing more to this tab until `feed.on`;
// the socket stays open. A page whose feed is off connects as `/ws/camera?paused=1`, so Sonny starts it off and
// no frame is encoded before the page could say so. When the socket closes it reconnects (reconnecting.ts:
// 0.5 s doubling up to 5 s, starting over once a frame arrives).
import { reconnecting, sameOriginUrl, type Backoff, type BasicSocket } from "../stream/reconnecting";
import { parseCameraMessage, type CameraControl, type CameraHeader } from "./message";

export interface CameraFrame {
  header: CameraHeader;
  image: ImageBitmap;
}

export interface CameraClient {
  setPaused(paused: boolean): void;
  close(): void;
}

export interface CameraSocket extends BasicSocket {
  binaryType: string;
}

export interface CameraOptions {
  /** Start with the feed off (the owner's remembered PAUSE FEED). */
  paused?: boolean;
  open?: (url: string) => CameraSocket;
  backoffS?: Backoff;
}

export function cameraUrl(location: Pick<Location, "protocol" | "host"> = window.location): string {
  return sameOriginUrl("/ws/camera", location);
}

export function connectCamera(url: string, onFrame: (frame: CameraFrame) => void, options: CameraOptions = {}): CameraClient {
  let paused = options.paused ?? false;
  let sonnyPaused = paused; // what Sonny was last told (or connected with)
  let closed = false;
  let decoding = false;
  let newestSeq = 0;

  const sync = (socket: CameraSocket | null) => {
    if (socket === null || sonnyPaused === paused) return;
    const control: CameraControl = { type: paused ? "feed.off" : "feed.on" };
    socket.send(JSON.stringify(control));
    sonnyPaused = paused;
  };

  const onMessage = async (data: unknown) => {
    if (!(data instanceof ArrayBuffer) || paused || decoding) return;
    const message = parseCameraMessage(data);
    if (message === null || message.header.seq <= newestSeq) return;
    conn.healthy(); // frames are flowing: the next drop starts the backoff over
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

  const conn = reconnecting<CameraSocket>({
    url: () => {
      sonnyPaused = paused;
      return paused ? `${url}?paused=1` : url;
    },
    open: options.open,
    backoffS: options.backoffS,
    onConnect: (s) => {
      s.binaryType = "arraybuffer";
      newestSeq = 0; // Sonny counts from 1 again after a restart
    },
    onOpen: sync, // a choice made while connecting
    onMessage: (data) => void onMessage(data),
  });

  return {
    setPaused(next) {
      paused = next;
      sync(conn.openSocket());
    },
    close() {
      closed = true;
      conn.close();
    },
  };
}

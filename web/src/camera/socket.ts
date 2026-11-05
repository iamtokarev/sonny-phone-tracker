// The page's /ws/camera client (spec §5). Each binary message is one frame; the newest wins (Sonny already
// skips frames for a slow tab, and so does the page: a frame still decoding is dropped for the next one).
// Pause sends `{"type": "pause"}` so Sonny encodes and sends nothing more to this tab until `resume`;
// the socket stays open. When it closes the client reconnects after 0.5 s, doubling up to 5 s.
import { parseCameraMessage, type CameraControl, type CameraHeader } from "./message";

export interface CameraFrame {
  header: CameraHeader;
  image: ImageBitmap;
}

export interface CameraClient {
  setPaused(paused: boolean): void;
  close(): void;
}

export function cameraUrl(location: Pick<Location, "protocol" | "host"> = window.location): string {
  return `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/camera`;
}

export function connectCamera(url: string, onFrame: (frame: CameraFrame) => void, backoffS = { first: 0.5, max: 5 }): CameraClient {
  let socket: WebSocket | null = null;
  let paused = false;
  let closed = false;
  let delayS = backoffS.first;
  let retry: ReturnType<typeof setTimeout> | null = null;
  let decoding = false;
  let newestSeq = 0;

  const send = (control: CameraControl) => {
    if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify(control));
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
    const s = new WebSocket(url);
    s.binaryType = "arraybuffer";
    socket = s;
    s.onopen = () => {
      delayS = backoffS.first;
      if (paused) send({ type: "pause" });
    };
    s.onmessage = (event) => {
      if (socket === s) void onMessage(event.data);
    };
    s.onclose = () => {
      if (socket !== s || closed) return;
      socket = null;
      retry = setTimeout(connect, delayS * 1000);
      delayS = Math.min(backoffS.max, delayS * 2);
    };
  };

  connect();

  return {
    setPaused(next) {
      if (next === paused) return;
      paused = next;
      send({ type: paused ? "pause" : "resume" });
    },
    close() {
      closed = true;
      if (retry !== null) clearTimeout(retry);
      socket?.close();
      socket = null;
    },
  };
}

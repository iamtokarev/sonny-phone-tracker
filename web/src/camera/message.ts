// One /ws/camera message (spec §5): `u32 header_len (little-endian) | JSON header | JPEG`.
// Hand-written to match the header documented in src/sonny_focus_companion/dashboard/camera.py; change both together.

/** A detection box in source-frame pixels (the frame is `w` x `h`; the JPEG may be smaller). */
export interface CameraBox {
  cls: "phone" | "person";
  conf: number;
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

export interface CameraHeader {
  /** Counts offered frames from 1; gaps are frames this tab skipped. */
  seq: number;
  /** The frame's arrival time, epoch seconds on this Mac's clock. */
  t: number;
  /** The source frame's size, the space the boxes are in. */
  w: number;
  h: number;
  /** The best phone and best person, as the attention rules read them; empty outside a session. */
  boxes: CameraBox[];
  /** The Attention State for this frame; null outside a session, during a Pause or while Blind. */
  attention: "focused" | "phone_use" | "away" | null;
  /** True when the detector ran on this frame (a Focus Session). */
  watching: boolean;
}

export interface CameraMessage {
  header: CameraHeader;
  jpeg: Uint8Array;
}

/** What the page sends to stop or restart its feed (Privacy). Not "pause": Pause is the owner's session Pause. */
export type CameraControl = { type: "feed.off" } | { type: "feed.on" };

/** The header and the JPEG bytes of one binary message, or null when it is malformed. */
export function parseCameraMessage(data: ArrayBuffer): CameraMessage | null {
  if (data.byteLength < 4) return null;
  const length = new DataView(data).getUint32(0, true);
  if (4 + length > data.byteLength) return null;
  let header: CameraHeader;
  try {
    header = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(new Uint8Array(data, 4, length)));
  } catch {
    return null;
  }
  if (typeof header !== "object" || header === null || !Array.isArray(header.boxes)) return null;
  return { header, jpeg: new Uint8Array(data, 4 + length) };
}

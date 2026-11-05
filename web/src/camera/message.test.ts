import { describe, expect, it } from "vitest";
import { parseCameraMessage } from "./message";

function message(header: object, jpeg: number[]): ArrayBuffer {
  const json = new TextEncoder().encode(JSON.stringify(header));
  const out = new Uint8Array(4 + json.length + jpeg.length);
  new DataView(out.buffer).setUint32(0, json.length, true);
  out.set(json, 4);
  out.set(jpeg, 4 + json.length);
  return out.buffer;
}

const header = {
  seq: 7,
  t: 1762300000.5,
  w: 1280,
  h: 720,
  boxes: [{ cls: "phone", conf: 0.91, x0: 600, y0: 400, x1: 700, y1: 560 }],
  attention: "phone_use",
  watching: true,
};

describe("parseCameraMessage", () => {
  it("splits a u32 LE header length, the JSON header and the JPEG bytes", () => {
    const parsed = parseCameraMessage(message(header, [0xff, 0xd8, 0xff, 0xd9]));
    expect(parsed?.header).toEqual(header);
    expect(Array.from(parsed!.jpeg)).toEqual([0xff, 0xd8, 0xff, 0xd9]);
  });

  it("refuses a message shorter than its header length says", () => {
    const broken = message(header, []).slice(0, 20);
    expect(parseCameraMessage(broken)).toBeNull();
  });

  it("refuses a header that is not JSON", () => {
    const out = new Uint8Array([3, 0, 0, 0, 0x7b, 0x7b, 0x7b, 0xff]);
    expect(parseCameraMessage(out.buffer)).toBeNull();
  });
});

import { describe, expect, it } from "vitest";
import { fitFrame, scaleBox } from "./geometry";

const phone = { cls: "phone" as const, conf: 0.9, x0: 640, y0: 360, x1: 800, y1: 600 };

describe("scaleBox", () => {
  it("scales source-frame pixels to a canvas of the same shape", () => {
    const fit = fitFrame(1280, 720, 640, 360);
    expect(scaleBox(phone, fit)).toEqual({ x: 320, y: 180, w: 80, h: 120 });
  });

  it("follows the letterbox when the frame's shape differs from the canvas", () => {
    // 4:3 source in a 16:9 canvas: scale 0.75, centred with 80 px either side.
    const fit = fitFrame(640, 480, 640, 360);
    expect(fit).toEqual({ x: 80, y: 0, w: 480, h: 360, scale: 0.75 });
    expect(scaleBox({ ...phone, x0: 0, y0: 0, x1: 640, y1: 480 }, fit)).toEqual({ x: 80, y: 0, w: 480, h: 360 });
  });

  it("does not depend on the JPEG's size, only the header's w and h", () => {
    // A 1920x1080 source sent as a 960x540 JPEG still has its boxes in 1920x1080 pixels.
    const fit = fitFrame(1920, 1080, 1440, 810);
    expect(scaleBox({ ...phone, x0: 960, y0: 540, x1: 1920, y1: 1080 }, fit)).toEqual({ x: 720, y: 405, w: 720, h: 405 });
  });
});

import { describe, expect, it } from "vitest";
import { ditherBayer, type Rgb } from "./dither";

const INK: Rgb = [17, 17, 17];
const PAPER: Rgb = [239, 237, 230];

function solid(w: number, h: number, rgb: Rgb): Uint8ClampedArray {
  const out = new Uint8ClampedArray(w * h * 4);
  for (let i = 0; i < w * h; i++) out.set([...rgb, 255], i * 4);
  return out;
}

/** The dithered image as rows of "#" (ink) and "." (paper). */
function pattern(rgba: Uint8ClampedArray, w: number): string[] {
  const rows: string[] = [];
  for (let i = 0; i < rgba.length / 4; i++) {
    if (i % w === 0) rows.push("");
    const px = Array.from(rgba.slice(i * 4, i * 4 + 4));
    if (px.join() === [...INK, 255].join()) rows[rows.length - 1] += "#";
    else if (px.join() === [...PAPER, 255].join()) rows[rows.length - 1] += ".";
    else throw new Error(`pixel ${i} is neither ink nor paper: ${px}`);
  }
  return rows;
}

describe("ditherBayer", () => {
  it("draws mid grey as the 4x4 Bayer checkerboard, in ink and paper only", () => {
    const out = ditherBayer(solid(4, 4, [128, 128, 128]), 4, 4, INK, PAPER);
    expect(pattern(out, 4)).toEqual([".#.#", "#.#.", ".#.#", "#.#."]);
  });

  it("draws black as all ink and white as all paper", () => {
    expect(pattern(ditherBayer(solid(4, 2, [0, 0, 0]), 4, 2, INK, PAPER), 4)).toEqual(["####", "####"]);
    expect(pattern(ditherBayer(solid(4, 2, [255, 255, 255]), 4, 2, INK, PAPER), 4)).toEqual(["....", "...."]);
  });

  it("weighs colour by luminance: pure blue is dark, pure green is light", () => {
    // Blue is 0.114 luminance: only the two lowest Bayer thresholds (0 and 1) let paper through.
    expect(pattern(ditherBayer(solid(4, 4, [0, 0, 255]), 4, 4, INK, PAPER), 4)).toEqual([
      ".###",
      "####",
      "##.#",
      "####",
    ]);
    // Green is 0.587: thresholds 0..8 are paper, 9 of 16 cells.
    const green = pattern(ditherBayer(solid(4, 4, [0, 255, 0]), 4, 4, INK, PAPER), 4).join("");
    expect(green.split("").filter((c) => c === ".").length).toBe(9);
  });

  it("tiles the matrix across a larger image", () => {
    const out = pattern(ditherBayer(solid(8, 1, [128, 128, 128]), 8, 1, INK, PAPER), 8);
    expect(out).toEqual([".#.#.#.#"]);
  });
});

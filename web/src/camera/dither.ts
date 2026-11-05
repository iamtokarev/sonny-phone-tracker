// Ordered 4x4 Bayer dither in two tones (spec §8), ported from board 02b's mock engine (sonny-sim.js).
// The caller downscales the frame first (one source pixel per dither cell) and draws the result back up
// with smoothing off.

export type Rgb = readonly [number, number, number];

/** The 4x4 Bayer matrix, row by row; cell (x, y) lets paper through when luminance > (m + 0.5) / 16. */
export const BAYER_4 = [0, 8, 2, 10, 12, 4, 14, 6, 3, 11, 1, 9, 15, 7, 13, 5] as const;

/** Bayer threshold for cell (x, y), in 0..1. */
export function bayerThreshold(x: number, y: number): number {
  return (BAYER_4[(y & 3) * 4 + (x & 3)] + 0.5) / 16;
}

/** An RGBA image as ink and paper only, thresholding Rec. 601 luminance against the Bayer matrix. */
export function ditherBayer(rgba: Uint8ClampedArray, w: number, h: number, ink: Rgb, paper: Rgb): Uint8ClampedArray {
  const out = new Uint8ClampedArray(w * h * 4);
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      const k = (y * w + x) * 4;
      const lum = (0.299 * rgba[k] + 0.587 * rgba[k + 1] + 0.114 * rgba[k + 2]) / 255;
      const c = lum > bayerThreshold(x, y) ? paper : ink;
      out[k] = c[0];
      out[k + 1] = c[1];
      out[k + 2] = c[2];
      out[k + 3] = 255;
    }
  }
  return out;
}

export function hexRgb(hex: string): Rgb {
  const h = hex.trim().replace("#", "");
  return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
}

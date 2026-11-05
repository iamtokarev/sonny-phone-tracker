// Where the camera frame and its boxes land on the canvas: the source frame is fitted inside the canvas
// (letterboxed when the shapes differ), and the boxes, in source-frame pixels (header `w` x `h`), follow it.
import type { CameraBox } from "./message";

/** The frame's rectangle on the canvas, in canvas pixels, and source pixels to canvas pixels. */
export interface Fit {
  x: number;
  y: number;
  w: number;
  h: number;
  scale: number;
}

export interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

export function fitFrame(srcW: number, srcH: number, canvasW: number, canvasH: number): Fit {
  const scale = Math.min(canvasW / srcW, canvasH / srcH);
  const w = srcW * scale;
  const h = srcH * scale;
  return { x: (canvasW - w) / 2, y: (canvasH - h) / 2, w, h, scale };
}

export function scaleBox(box: CameraBox, fit: Fit): Rect {
  return {
    x: fit.x + box.x0 * fit.scale,
    y: fit.y + box.y0 * fit.scale,
    w: (box.x1 - box.x0) * fit.scale,
    h: (box.y1 - box.y0) * fit.scale,
  };
}

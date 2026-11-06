// What the main view's canvases share: the design tokens as canvas colours, prefers-reduced-motion, and
// one requestAnimationFrame loop per canvas.
import { useEffect, useRef, useSyncExternalStore } from "react";
import { hexRgb } from "../camera/dither";
import type { Palette } from "../camera/draw";

/** tokens.css's colours, read once from :root (canvas can't take var()). */
export function readPalette(): Palette {
  const css = getComputedStyle(document.documentElement);
  const token = (name: string, fallback: string) => css.getPropertyValue(name).trim() || fallback;
  const ink = token("--ink", "#111111");
  const paper = token("--paper", "#efede6");
  return {
    ink,
    paper,
    accent: token("--accent", "#ff4d00"),
    inkRgb: hexRgb(ink),
    paperRgb: hexRgb(paper),
    font: token("--font-mono", "monospace"),
  };
}

const REDUCE = "(prefers-reduced-motion: reduce)";

function subscribeMotion(onChange: () => void): () => void {
  const query = window.matchMedia(REDUCE);
  query.addEventListener("change", onChange);
  return () => query.removeEventListener("change", onChange);
}

export function usePrefersReducedMotion(): boolean {
  return useSyncExternalStore(subscribeMotion, () => window.matchMedia(REDUCE).matches);
}

/** requestAnimationFrame and cancelAnimationFrame. */
export interface Frames {
  request(cb: FrameRequestCallback): number;
  cancel(id: number): void;
}

const browserFrames: Frames = {
  request: (cb) => requestAnimationFrame(cb),
  cancel: (id) => cancelAnimationFrame(id),
};

/** Calls `draw(seconds)` on every animation frame until the returned stop; nothing at all while inactive. */
export function frameLoop(active: boolean, draw: (t: number) => void, frames: Frames = browserFrames): () => void {
  if (!active) return () => {};
  let id = frames.request(function loop(now) {
    draw(now / 1000);
    id = frames.request(loop);
  });
  return () => frames.cancel(id);
}

/** Calls the latest `draw(seconds)` on every animation frame while mounted and `active` (a hidden canvas,
 *  such as the normal view's under the expanded camera, passes false and draws nothing). */
export function useAnimationFrame(draw: (t: number) => void, active = true) {
  const latest = useRef(draw);
  useEffect(() => {
    latest.current = draw;
  });
  useEffect(() => frameLoop(active, (t) => latest.current(t)), [active]);
}

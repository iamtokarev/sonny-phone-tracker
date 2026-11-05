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

/** Calls the latest `draw(seconds)` on every animation frame while mounted. */
export function useAnimationFrame(draw: (t: number) => void) {
  const latest = useRef(draw);
  useEffect(() => {
    latest.current = draw;
  });
  useEffect(() => {
    let id = requestAnimationFrame(function loop(now) {
      latest.current(now / 1000);
      id = requestAnimationFrame(loop);
    });
    return () => cancelAnimationFrame(id);
  }, []);
}

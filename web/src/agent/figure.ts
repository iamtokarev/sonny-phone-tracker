// The agent figure (spec §8): a grid of dithered square blocks, ported from board 02b's mock engine
// (`drawOrb`, style "blocks", grid 11). Idle breathes slowly, Listening ripples outwards, Thinking scrambles
// under a sweep, Speaking pulses. Under prefers-reduced-motion the page draws one still frame (`t = 0`).
import type { AgentView } from "../protocol.gen";
import { bayerThreshold } from "../camera/dither";

export type Phase = AgentView["phase"];

export const GRID = 11;
const R = 0.86;

/** The mock engine's cheap hash: a stable pseudo-random 0..1 per (a, b, c). */
function hash(a: number, b: number, c: number): number {
  const h = Math.sin(a * 127.1 + b * 311.7 + c * 74.7) * 43758.5453;
  return h - Math.floor(h);
}

/** How lit block (i, j) is, 0..1, in `phase` at `t` seconds. */
export function blockLevel(phase: Phase, t: number, i: number, j: number, n = GRID): number {
  const u = ((i + 0.5) / n) * 2 - 1;
  const v = ((j + 0.5) / n) * 2 - 1;
  const r = Math.sqrt(u * u + v * v);
  let val: number;
  if (phase === "idle") {
    val = Math.max(0, 1 - r / (R * (0.82 + 0.05 * Math.sin(t * 1.1)))) * 0.95;
  } else if (phase === "listening") {
    val = r < R ? (0.5 + 0.5 * Math.sin(r * 13 - t * 7)) * (1 - (r / R) * 0.6) : 0;
  } else if (phase === "thinking") {
    const sweep = (Math.cos(Math.atan2(v, u) - t * 4) + 1) / 2;
    val = r < R ? hash(i, j, Math.floor(t * 14)) * (0.35 + 0.65 * sweep) : 0;
  } else {
    // Speaking: no audio level reaches the page, so the pulse is the mock's synthetic one.
    const amp = 0.55 + 0.45 * Math.abs(Math.sin(t * 9.3) * Math.sin(t * 3.1 + 1));
    const rr = R * (0.45 + 0.55 * amp);
    val = r < rr ? Math.pow(1 - r / rr, 0.6) : 0;
    val *= 0.75 + 0.25 * Math.sin(r * 18 - t * 12);
  }
  return Math.max(0, Math.min(1, val));
}

/** Draw the figure filling the canvas's square middle; `fg` for lit blocks, `dim` for the unlit disc. */
export function drawFigure(ctx: CanvasRenderingContext2D, w: number, h: number, phase: Phase, t: number, fg: string, dim: string) {
  ctx.clearRect(0, 0, w, h);
  const cs = Math.min(w, h) / GRID;
  const ox = (w - cs * GRID) / 2;
  const oy = (h - cs * GRID) / 2;
  for (let j = 0; j < GRID; j++) {
    for (let i = 0; i < GRID; i++) {
      const u = ((i + 0.5) / GRID) * 2 - 1;
      const v = ((j + 0.5) / GRID) * 2 - 1;
      const on = blockLevel(phase, t, i, j) > bayerThreshold(i, j);
      if (!on && Math.sqrt(u * u + v * v) >= 1) continue;
      ctx.fillStyle = on ? fg : dim;
      const cx = ox + (i + 0.5) * cs;
      const cy = oy + (j + 0.5) * cs;
      ctx.fillRect(cx - cs * 0.42, cy - cs * 0.42, cs * 0.84, cs * 0.84);
    }
  }
}

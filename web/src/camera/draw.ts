// Drawing the camera canvas (spec §8), ported from board 02b's mock engine (`drawCamera`): the frame raw or
// dithered, then the boxes over it at full resolution; static while Blind; stripes while the feed is paused.
import { ditherBayer, type Rgb } from "./dither";
import { fitFrame, scaleBox } from "./geometry";
import type { CameraFrame } from "./socket";

export type CameraMode = "dither" | "raw";

export interface Palette {
  ink: string;
  paper: string;
  accent: string;
  inkRgb: Rgb;
  paperRgb: Rgb;
  font: string;
}

/** CSS pixels per dither cell, as on board 02b. */
const CELL = 4;

let scratch: HTMLCanvasElement | null = null;
function scratchCanvas(w: number, h: number): HTMLCanvasElement {
  scratch ??= document.createElement("canvas");
  if (scratch.width !== w || scratch.height !== h) {
    scratch.width = w;
    scratch.height = h;
  }
  return scratch;
}

/** Size the canvas's backing store to its CSS box (device pixels, capped at 2x); returns the scale. */
export function fitCanvas(canvas: HTMLCanvasElement): number {
  const dpr = Math.min(2, window.devicePixelRatio || 1);
  const w = Math.max(10, Math.round(canvas.clientWidth * dpr));
  const h = Math.max(10, Math.round(canvas.clientHeight * dpr));
  if (canvas.width !== w || canvas.height !== h) {
    canvas.width = w;
    canvas.height = h;
  }
  return dpr;
}

/** The picture alone, raw or dithered, letterboxed in ink. Redrawn only when the frame, size or mode changes. */
export function drawPicture(ctx: CanvasRenderingContext2D, frame: CameraFrame, p: Palette, mode: CameraMode, dpr: number) {
  const W = ctx.canvas.width;
  const H = ctx.canvas.height;
  const { image } = frame;
  ctx.fillStyle = p.ink;
  ctx.fillRect(0, 0, W, H);
  const fit = fitFrame(image.width, image.height, W, H);

  if (mode === "raw") {
    ctx.imageSmoothingEnabled = true;
    ctx.drawImage(image, fit.x, fit.y, fit.w, fit.h);
  } else {
    const cell = CELL * dpr;
    const cw = Math.max(16, Math.floor(fit.w / cell));
    const ch = Math.max(9, Math.floor(fit.h / cell));
    const small = scratchCanvas(cw, ch);
    const sctx = small.getContext("2d", { willReadFrequently: true })!;
    sctx.imageSmoothingEnabled = true;
    sctx.drawImage(image, 0, 0, cw, ch);
    const dithered = ditherBayer(sctx.getImageData(0, 0, cw, ch).data, cw, ch, p.inkRgb, p.paperRgb);
    sctx.putImageData(new ImageData(dithered as Uint8ClampedArray<ArrayBuffer>, cw, ch), 0, 0);
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(small, fit.x, fit.y, fit.w, fit.h);
  }
}

export interface BoxOptions {
  /** Phone Use: the phone box glows and pulses. */
  hot: boolean;
  /** Seconds, for the pulse; null under prefers-reduced-motion. */
  t: number | null;
  dpr: number;
}

/** The frame's boxes over the picture, at the canvas's full resolution: person in paper, phone in accent. */
export function drawBoxes(ctx: CanvasRenderingContext2D, frame: CameraFrame, p: Palette, o: BoxOptions) {
  const W = ctx.canvas.width;
  const H = ctx.canvas.height;
  const { header } = frame;
  if (!header.watching) return;
  // Boxes are in source-frame pixels (header w x h), whatever size the JPEG came at.
  const boxFit = fitFrame(header.w, header.h, W, H);
  const lw = 3 * o.dpr;
  for (const box of header.boxes) {
    const r = scaleBox(box, boxFit);
    const label = `${box.cls.toUpperCase()} ${box.conf.toFixed(2)}`;
    ctx.save();
    if (box.cls === "phone") {
      if (o.hot) {
        ctx.globalAlpha = o.t === null ? 1 : 0.55 + 0.45 * Math.abs(Math.sin(o.t * 3));
        ctx.shadowColor = p.accent;
        ctx.shadowBlur = 14 * o.dpr;
      }
      drawBox(ctx, r, label, p.accent, p.ink, o.hot ? lw * 1.6 : lw, o.dpr, p.font);
    } else {
      drawBox(ctx, r, label, p.paper, p.ink, lw, o.dpr, p.font);
    }
    ctx.restore();
  }
}

function drawBox(
  ctx: CanvasRenderingContext2D,
  r: { x: number; y: number; w: number; h: number },
  label: string,
  color: string,
  labelInk: string,
  lineWidth: number,
  dpr: number,
  font: string,
) {
  ctx.strokeStyle = color;
  ctx.lineWidth = lineWidth;
  ctx.strokeRect(r.x, r.y, r.w, r.h);
  ctx.shadowBlur = 0;
  ctx.font = `700 ${Math.round(10 * dpr)}px ${font}`;
  const tw = ctx.measureText(label).width + 8 * dpr;
  const th = 15 * dpr;
  const ty = Math.max(0, r.y - th);
  ctx.fillStyle = color;
  ctx.fillRect(r.x - lineWidth / 2, ty, tw, th);
  ctx.fillStyle = labelInk;
  ctx.textBaseline = "middle";
  ctx.fillText(label, r.x - lineWidth / 2 + 4 * dpr, ty + th / 2 + 0.5);
}

/** Blind: two-tone static with a rolling bar; one still frame when `t` is null. */
export function drawStatic(ctx: CanvasRenderingContext2D, p: Palette, t: number | null) {
  const bw = 160;
  const bh = 90;
  const small = scratchCanvas(bw, bh);
  const sctx = small.getContext("2d", { willReadFrequently: true })!;
  const im = sctx.createImageData(bw, bh);
  const roll = t === null ? 0 : Math.floor(t * 30);
  let seed = t === null ? 1 : Math.floor(t * 1000);
  const random = () => {
    seed = (seed * 1103515245 + 12345) & 0x7fffffff;
    return seed / 0x7fffffff;
  };
  for (let i = 0; i < bw * bh; i++) {
    const row = Math.floor(i / bw);
    const c = random() < 0.5 || (row + roll) % 9 === 0 ? p.paperRgb : p.inkRgb;
    im.data[i * 4] = c[0];
    im.data[i * 4 + 1] = c[1];
    im.data[i * 4 + 2] = c[2];
    im.data[i * 4 + 3] = 255;
  }
  sctx.putImageData(im, 0, 0);
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(small, 0, 0, ctx.canvas.width, ctx.canvas.height);
}

/** No picture (paused, or nothing received yet): ink with faint paper diagonals. */
export function drawBlank(ctx: CanvasRenderingContext2D, p: Palette, dpr: number) {
  const W = ctx.canvas.width;
  const H = ctx.canvas.height;
  ctx.fillStyle = p.ink;
  ctx.fillRect(0, 0, W, H);
  ctx.strokeStyle = `rgba(${p.paperRgb.join(",")},0.12)`;
  ctx.lineWidth = 1;
  for (let d = -H; d < W; d += 14 * dpr) {
    ctx.beginPath();
    ctx.moveTo(d, H);
    ctx.lineTo(d + H, 0);
    ctx.stroke();
  }
}

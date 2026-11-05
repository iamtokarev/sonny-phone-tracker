// The Camera block (spec §7, §8): what Sonny's camera sees, dithered by default, with RAW / DITHER and
// PAUSE FEED / RESUME FEED. Blind shows static; no new frame for 2 s shows the feed as frozen.
import { useEffect, useRef, useState } from "react";
import { readPalette, useAnimationFrame, usePrefersReducedMotion } from "../main/canvas";
import styles from "./CameraBlock.module.css";
import { drawBlank, drawBoxes, drawPicture, drawStatic, fitCanvas, type CameraMode, type Palette } from "./draw";
import { pageFeedOff } from "./feedOff";
import { cameraUrl, connectCamera, type CameraClient, type CameraFrame } from "./socket";

/** Seconds without a new frame before the feed shows as frozen (spec §5). */
export const FROZEN_AFTER_S = 2;

type Notice = "paused" | "frozen" | "none" | null;

export function CameraBlock({ blind, hot, dimmed }: { blind: boolean; hot: boolean; dimmed: boolean }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const client = useRef<CameraClient | null>(null);
  const frame = useRef<CameraFrame | null>(null);
  const base = useRef<{ canvas: HTMLCanvasElement; key: string } | null>(null);
  const palette = useRef<Palette | null>(null);
  const [mode, setMode] = useState<CameraMode>("dither");
  const [paused, setPaused] = useState(pageFeedOff.read); // a reload keeps the owner's PAUSE FEED
  const [notice, setNotice] = useState<Notice>(null);
  const reduced = usePrefersReducedMotion();

  useEffect(() => {
    const startPaused = pageFeedOff.read();
    const c = connectCamera(
      cameraUrl(),
      (next) => {
        frame.current?.image.close();
        frame.current = next;
      },
      { paused: startPaused },
    );
    client.current = c;
    return () => {
      c.close();
      client.current = null;
      frame.current?.image.close();
      frame.current = null;
    };
  }, []);

  useEffect(() => {
    client.current?.setPaused(paused);
    pageFeedOff.save(paused);
    if (paused) {
      frame.current?.image.close();
      frame.current = null;
    }
  }, [paused]);

  useAnimationFrame((t) => {
    const c = canvas.current;
    if (c === null) return;
    const dpr = fitCanvas(c);
    palette.current ??= readPalette();
    const p = palette.current;
    const ctx = c.getContext("2d")!;
    const f = frame.current;
    const age = f === null ? Infinity : Date.now() / 1000 - f.header.t;
    const next: Notice = paused ? "paused" : blind ? null : f === null ? "none" : age > FROZEN_AFTER_S ? "frozen" : null;
    if (next !== notice) setNotice(next);

    if (paused) return drawBlank(ctx, p, dpr);
    if (blind) return drawStatic(ctx, p, reduced ? null : t);
    if (f === null) return drawBlank(ctx, p, dpr);

    // The picture is redrawn only for a new frame, size or mode; the boxes every frame (the hot pulse).
    const key = `${f.header.seq}:${c.width}x${c.height}:${mode}`;
    if (base.current === null) base.current = { canvas: document.createElement("canvas"), key: "" };
    const b = base.current;
    if (b.key !== key) {
      b.canvas.width = c.width;
      b.canvas.height = c.height;
      drawPicture(b.canvas.getContext("2d")!, f, p, mode, dpr);
      b.key = key;
    }
    ctx.drawImage(b.canvas, 0, 0);
    drawBoxes(ctx, f, p, { hot, t: reduced || next === "frozen" ? null : t, dpr }); // a frozen frame stays still
  });

  return (
    <section className={styles.block} data-dimmed={dimmed || undefined} aria-label="Camera">
      <div className={styles.view}>
        <canvas ref={canvas} className={styles.canvas} role="img" aria-label="What Sonny's camera sees, with detection boxes" />
        {notice !== null && (
          <div className={styles.notice}>
            <span>{notice === "paused" ? "Feed paused" : notice === "frozen" ? "Feed frozen" : "No feed"}</span>
          </div>
        )}
      </div>
      <div className={styles.controls}>
        <button
          type="button"
          className={styles.toggle}
          aria-pressed={mode === "raw"}
          onClick={() => setMode((m) => (m === "raw" ? "dither" : "raw"))}
        >
          {mode === "raw" ? "Dither" : "Raw"}
        </button>
        <button type="button" className={styles.toggle} aria-pressed={paused} onClick={() => setPaused((v) => !v)}>
          {paused ? "Resume feed" : "Pause feed"}
        </button>
      </div>
    </section>
  );
}

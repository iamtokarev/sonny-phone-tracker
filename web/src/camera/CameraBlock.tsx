// The Camera block (spec §7, §8): what Sonny's camera sees, dithered by default, with RAW / DITHER,
// PAUSE FEED / RESUME FEED and (v2 spec §3) EXPAND. Blind shows static; no new frame for 2 s shows the feed
// as frozen. The page holds one camera feed (`useCameraFeed`: the /ws/camera socket, the newest frame and
// the owner's raw and feed-off choices); the block and the expanded view both draw it with `CameraView`,
// so expanding or collapsing never reconnects or forgets a choice.
import { useEffect, useRef, useState, type RefObject } from "react";
import { readPalette, useAnimationFrame, usePrefersReducedMotion } from "../main/canvas";
import styles from "./CameraBlock.module.css";
import { CELL, drawBlank, drawBoxes, drawPicture, drawStatic, fitCanvas, type CameraMode, type Palette } from "./draw";
import { pageFeedOff } from "./feedOff";
import { cameraUrl, connectCamera, type CameraClient, type CameraFrame } from "./socket";

/** Seconds without a new frame before the feed shows as frozen (spec §5). */
export const FROZEN_AFTER_S = 2;

/** CSS pixels per dither cell in the expanded camera (board V2Expanded). */
const EXPANDED_CELL = 5;

type Notice = "paused" | "frozen" | "none" | null;

export interface CameraFeed {
  /** The newest decoded frame, or null (none yet, or the feed is off). */
  frame: RefObject<CameraFrame | null>;
  mode: CameraMode;
  toggleMode(): void;
  paused: boolean;
  togglePaused(): void;
}

/** The page's /ws/camera feed. Call once, above whatever draws it. */
export function useCameraFeed(): CameraFeed {
  const client = useRef<CameraClient | null>(null);
  const frame = useRef<CameraFrame | null>(null);
  const [mode, setMode] = useState<CameraMode>("dither");
  const [paused, setPaused] = useState(pageFeedOff.read); // a reload keeps the owner's PAUSE FEED

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

  return {
    frame,
    mode,
    toggleMode: () => setMode((m) => (m === "raw" ? "dither" : "raw")),
    paused,
    togglePaused: () => setPaused((v) => !v),
  };
}

export interface CameraViewProps {
  feed: CameraFeed;
  blind: boolean;
  hot: boolean;
  /** Drawn in the expanded view: bigger dither cells and notice, and COLLAPSE instead of EXPAND. */
  expanded: boolean;
  onExpandToggle: () => void;
}

/** The picture (letterboxed 16:9), its notice and the RAW, PAUSE FEED and EXPAND / COLLAPSE buttons. */
export function CameraView({ feed, blind, hot, expanded, onExpandToggle }: CameraViewProps) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const base = useRef<{ canvas: HTMLCanvasElement; key: string } | null>(null);
  const palette = useRef<Palette | null>(null);
  const [notice, setNotice] = useState<Notice>(null);
  const reduced = usePrefersReducedMotion();
  const { mode, paused } = feed;

  useAnimationFrame((t) => {
    const c = canvas.current;
    if (c === null) return;
    const dpr = fitCanvas(c);
    palette.current ??= readPalette();
    const p = palette.current;
    const ctx = c.getContext("2d")!;
    const f = feed.frame.current;
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
      drawPicture(b.canvas.getContext("2d")!, f, p, mode, dpr, expanded ? EXPANDED_CELL : CELL);
      b.key = key;
    }
    ctx.drawImage(b.canvas, 0, 0);
    drawBoxes(ctx, f, p, { hot, t: reduced || next === "frozen" ? null : t, dpr }); // a frozen frame stays still
  });

  return (
    <>
      <div className={styles.view}>
        <canvas
          ref={canvas}
          className={styles.canvas}
          role="img"
          aria-label={expanded ? "What Sonny's camera sees, expanded, with detection boxes" : "What Sonny's camera sees, with detection boxes"}
        />
        {notice !== null && (
          <div className={styles.notice} data-expanded={expanded || undefined}>
            <span>{notice === "paused" ? "Feed paused" : notice === "frozen" ? "Feed frozen" : "No feed"}</span>
          </div>
        )}
      </div>
      <div className={styles.controls}>
        <button type="button" className={styles.toggle} aria-pressed={mode === "raw"} onClick={feed.toggleMode}>
          {mode === "raw" ? "Dither" : "Raw"}
        </button>
        <button type="button" className={styles.toggle} aria-pressed={paused} onClick={feed.togglePaused}>
          {paused ? "Resume feed" : "Pause feed"}
        </button>
        <button type="button" className={styles.expand} aria-pressed={expanded} data-camera-expand onClick={onExpandToggle}>
          <span aria-hidden="true">{expanded ? "[↙]" : "[↗]"}</span> {expanded ? "Collapse · Esc" : "Expand"}
        </button>
      </div>
    </>
  );
}

export interface CameraBlockProps {
  feed: CameraFeed;
  blind: boolean;
  hot: boolean;
  dimmed: boolean;
  onExpand: () => void;
}

export function CameraBlock({ feed, blind, hot, dimmed, onExpand }: CameraBlockProps) {
  return (
    <section className={styles.block} data-dimmed={dimmed || undefined} aria-label="Camera">
      <CameraView feed={feed} blind={blind} hot={hot} expanded={false} onExpandToggle={onExpand} />
    </section>
  );
}

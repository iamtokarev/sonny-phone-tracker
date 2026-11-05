// The Sonny block (spec §7): the agent figure, the phase word, and Sonny's latest line as one caption.
import { useRef } from "react";
import { fitCanvas } from "../camera/draw";
import { readPalette, useAnimationFrame, usePrefersReducedMotion } from "../main/canvas";
import type { Caption } from "../main/view";
import { drawFigure, type Phase } from "./figure";
import styles from "./SonnyBlock.module.css";

const PHASE_WORD: Record<Phase, string> = {
  idle: "Idle",
  listening: "Listening",
  thinking: "Thinking",
  speaking: "Speaking",
};

export function SonnyBlock({ phase, caption, dimmed }: { phase: Phase; caption: Caption; dimmed: boolean }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const reduced = usePrefersReducedMotion();
  const palette = useRef<ReturnType<typeof readPalette> | null>(null);

  useAnimationFrame((t) => {
    const c = canvas.current;
    if (c === null) return;
    fitCanvas(c);
    palette.current ??= readPalette();
    const { ink } = palette.current;
    // Reduced motion: one still form per phase (t = 0), with the phase word saying the rest.
    drawFigure(c.getContext("2d")!, c.width, c.height, phase, reduced ? 0 : t, ink, "rgba(17,17,17,0.07)");
  });

  return (
    <section className={styles.block} data-dimmed={dimmed || undefined} aria-label="Sonny">
      <div className={styles.agent}>
        <canvas ref={canvas} className={styles.figure} aria-hidden="true" />
        <span className={styles.phase} aria-live="polite">
          {PHASE_WORD[phase]}
        </span>
      </div>
      <p className={styles.caption} data-live={caption.live || undefined}>
        {caption.text}
      </p>
    </section>
  );
}

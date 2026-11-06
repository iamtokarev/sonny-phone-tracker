// The Sonny block (spec §7): the agent figure, the phase word, and Sonny's latest line as one caption.
import { useRef } from "react";
import { fitCanvas } from "../camera/draw";
import { readPalette, useAnimationFrame, usePrefersReducedMotion } from "../main/canvas";
import type { Caption } from "../main/view";
import { drawFigure, type Phase } from "./figure";
import styles from "./SonnyBlock.module.css";

export const PHASE_WORD: Record<Phase, string> = {
  idle: "Idle",
  listening: "Listening",
  thinking: "Thinking",
  speaking: "Speaking",
};

/** The agent figure for `phase`, drawn on its own canvas (sized by `className`); not drawn while `active` is
 *  false (hidden). */
export function AgentFigure({ phase, className, active = true }: { phase: Phase; className?: string; active?: boolean }) {
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
  }, active);

  return <canvas ref={canvas} className={className} aria-hidden="true" />;
}

export interface SonnyBlockProps {
  phase: Phase;
  caption: Caption;
  dimmed: boolean;
  /** False while hidden (the camera is expanded): the figure stops drawing. */
  active?: boolean;
}

export function SonnyBlock({ phase, caption, dimmed, active = true }: SonnyBlockProps) {
  return (
    <section className={styles.block} data-dimmed={dimmed || undefined} aria-label="Sonny">
      <div className={styles.agent}>
        <AgentFigure phase={phase} className={styles.figure} active={active} />
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

// One bar under the timer, both progress and history (spec §7): it fills left to right with a segment per
// Attention State, and a ▼ over it for each Nudge.
import styles from "./MainView.module.css";
import type { Timeline } from "./timeline";

const pct = (s: number, duration: number) => `${((s / duration) * 100).toFixed(3)}%`;

export function AttentionStrip({ timeline, durationS }: { timeline: Timeline | null; durationS: number }) {
  const label =
    timeline === null ? "No Focus Session" : `${Math.round((timeline.elapsedS / durationS) * 100)}% of the session gone`;
  return (
    <div className={styles.strip} role="img" aria-label={label}>
      {timeline?.segments.map((seg) => (
        <div
          key={`${seg.state}-${seg.from}`}
          className={styles.segment}
          data-state={seg.state}
          style={{ left: pct(seg.from, durationS), width: pct(seg.to - seg.from, durationS) }}
        />
      ))}
      {timeline?.nudges.map((at, i) => (
        <span key={`${i}-${at}`} className={styles.nudge} style={{ left: pct(at, durationS) }} aria-hidden="true">
          ▼
        </span>
      ))}
    </div>
  );
}

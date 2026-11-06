// The Attention block (spec §7): the state word; during a Distraction Episode its time and the three
// Escalation Ladder squares; while Resting, how to start; once a session ended, its summary.
import type { EndedView, EpisodeView } from "../protocol.gen";
import { formatClock } from "../clock/timer";
import { endedSummary } from "./clear";
import styles from "./MainView.module.css";
import { attentionWord, type Scene } from "./view";

/** The ladder step an episode is on: the first square from the start, one more per Nudge, three at most. */
export function ladderStep(episode: EpisodeView): number {
  return Math.min(3, episode.nudges.length + 1);
}

export interface AttentionBlockProps {
  scene: Scene;
  episode: EpisodeView | null;
  nowS: number;
  /** The ended session the page shows, if any: its word and its summary (clear.ts). */
  ended?: EndedView | null;
}

export function AttentionBlock({ scene, episode, nowS, ended = null }: AttentionBlockProps) {
  const inSession = scene !== "resting" && scene !== "ended";
  const step = episode !== null && inSession ? ladderStep(episode) : 0;
  return (
    <section className={styles.attention} data-scene={scene} aria-label="Attention">
      <span className={styles.label}>Attention</span>
      <span className={styles.stateWord} role="status">
        <span>{attentionWord(scene, ended)}</span>
      </span>
      {episode !== null && inSession && (
        <span className={styles.episode}>
          <span>{formatClock(nowS - episode.started_at)}</span>
          <span className={styles.ladder} aria-label={`Escalation step ${step} of 3`}>
            {[1, 2, 3].map((n) => (
              <span key={n} className={styles.rung} data-on={step >= n || undefined} data-top={n === 3 || undefined} />
            ))}
          </span>
        </span>
      )}
      {scene === "resting" && <span className={styles.hint}>Say “Hey Sonny”.</span>}
      {scene === "ended" && ended !== null && <span className={styles.hint}>{endedSummary(ended)}</span>}
    </section>
  );
}

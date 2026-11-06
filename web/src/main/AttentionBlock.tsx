// The Attention block (spec §7): the state word; during a Distraction Episode its time and the three
// Escalation Ladder squares; while Resting, how to start; once a session ended, its summary.
import type { EndedView, EpisodeView } from "../protocol.gen";
import { formatClock } from "../clock/timer";
import { endedSummary } from "./clear";
import fill from "./AttentionFill.module.css";
import styles from "./MainView.module.css";
import { attentionWord, type Scene } from "./view";

/** The ladder step an episode is on: the first square from the start, one more per Nudge, three at most. */
export function ladderStep(episode: EpisodeView): number {
  return Math.min(3, episode.nudges.length + 1);
}

/** The Escalation Ladder's three squares at `step`, in the colour of the words around them. */
export function EscalationLadder({ step, scene }: { step: number; scene: Scene }) {
  return (
    <span className={fill.ladder} data-scene={scene} aria-label={`Escalation step ${step} of 3`}>
      {[1, 2, 3].map((n) => (
        <span key={n} className={fill.rung} data-on={step >= n || undefined} data-top={n === 3 || undefined} />
      ))}
    </span>
  );
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
    <section className={`${styles.attention} ${fill.fill}`} data-scene={scene} aria-label="Attention">
      <span className={`${styles.label} ${fill.onPaper}`}>Attention</span>
      <span className={styles.stateWord} role="status">
        <span className={fill.onPaper}>{attentionWord(scene, ended)}</span>
      </span>
      {episode !== null && inSession && (
        <span className={`${styles.episode} ${fill.onPaper}`}>
          <span>{formatClock(nowS - episode.started_at)}</span>
          <EscalationLadder step={step} scene={scene} />
        </span>
      )}
      {scene === "resting" && <span className={styles.hint}>Say “Hey Sonny”.</span>}
      {scene === "ended" && ended !== null && <span className={styles.hint}>{endedSummary(ended)}</span>}
    </section>
  );
}

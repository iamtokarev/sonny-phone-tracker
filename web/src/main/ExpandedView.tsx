// The expanded camera (v2 spec §3, board V2Expanded), below the header: a compact bar with the task, time
// remaining, the Attention State chip, the episode time and Escalation Ladder, and the agent figure and phase;
// then the camera at full width filling the rest of the window. It draws the page's one camera feed.
import { AgentFigure, PHASE_WORD } from "../agent/SonnyBlock";
import type { Phase } from "../agent/figure";
import { CameraView, type CameraFeed } from "../camera/CameraBlock";
import { formatClock, remainingS } from "../clock/timer";
import type { EpisodeView, SessionView } from "../protocol.gen";
import { ladderStep } from "./AttentionBlock";
import styles from "./ExpandedView.module.css";
import { SCENE_WORD, type Scene } from "./view";

export interface ExpandedViewProps {
  session: SessionView | null;
  scene: Scene;
  episode: EpisodeView | null;
  phase: Phase;
  feed: CameraFeed;
  /** Epoch seconds on Sonny's clock. */
  nowS: number;
  onCollapse: () => void;
}

export function ExpandedView({ session, scene, episode, phase, feed, nowS, onCollapse }: ExpandedViewProps) {
  const inSession = scene !== "resting" && scene !== "ended";
  const step = episode !== null && inSession ? ladderStep(episode) : 0;
  const paused = session !== null && session.paused_since !== null && inSession;

  return (
    <>
      <section className={styles.bar} aria-label="Focus Session">
        <div className={styles.session}>
          <span className={styles.task}>
            {session === null ? "No Focus Session" : `${session.task} / ${formatClock(session.duration_s)} block`}
            {paused && " · Paused"}
          </span>
          <span className={styles.timer} data-scene={scene} aria-label="Time remaining">
            {formatClock(remainingS(session, nowS) ?? 0)}
          </span>
        </div>
        <span className={styles.chip} data-scene={scene} role="status" aria-label="Attention">
          {SCENE_WORD[scene]}
        </span>
        {step > 0 && episode !== null && (
          <span className={styles.episode}>
            <span>{formatClock(nowS - episode.started_at)}</span>
            <span className={styles.ladder} data-scene={scene} aria-label={`Escalation step ${step} of 3`}>
              {[1, 2, 3].map((n) => (
                <span key={n} className={styles.rung} data-on={step >= n || undefined} data-top={n === 3 || undefined} />
              ))}
            </span>
          </span>
        )}
        <span className={styles.agent} data-dimmed={scene === "away" || undefined}>
          <AgentFigure phase={phase} className={styles.figure} />
          <span className={styles.phase} aria-live="polite">
            {PHASE_WORD[phase]}
          </span>
        </span>
      </section>
      <section className={styles.camera} data-dimmed={scene === "away" || undefined} aria-label="Camera, expanded">
        <CameraView feed={feed} blind={scene === "blind"} hot={scene === "phone_use"} expanded onExpandToggle={onCollapse} />
      </section>
    </>
  );
}

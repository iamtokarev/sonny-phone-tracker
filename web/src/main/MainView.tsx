// The main view, board 02b (spec §7): header and status line, task line, timer, attention strip, and the
// Attention, Sonny and Camera blocks in one row. One screen at 1440x900; Details opens below it.
import { useState, type ReactNode } from "react";
import { SonnyBlock } from "../agent/SonnyBlock";
import { CameraBlock } from "../camera/CameraBlock";
import { formatClock, remainingS } from "../clock/timer";
import type { Envelope } from "../protocol.gen";
import type { LiveState } from "../stream/feed";
import { AttentionBlock } from "./AttentionBlock";
import { AttentionStrip } from "./AttentionStrip";
import styles from "./MainView.module.css";
import { buildTimeline, mergeHistory } from "./timeline";
import { caption, scene, statusLine } from "./view";

export interface MainViewProps {
  state: LiveState;
  /** Epoch seconds on Sonny's clock. */
  nowS: number;
  /** The Details toggle, drawn at the right of the header (ticket 08). */
  detailsToggle?: ReactNode;
}

export function MainView({ state, nowS, detailsToggle }: MainViewProps) {
  // The strip's records outlive `recent`'s 500-envelope window (see timeline.ts).
  const [history, setHistory] = useState<Envelope[]>([]);
  const merged = mergeHistory(history, state.recent, state.session);
  if (merged !== history) setHistory(merged);

  const session = state.session;
  const s = scene(state);
  const status = statusLine(state, nowS);
  const away = s === "away";
  const timeline = session === null ? null : buildTimeline(merged, session, nowS);
  const paused = session !== null && session.paused_since !== null && s !== "ended";

  return (
    <div className={styles.main} data-scene={s}>
      <header className={styles.header}>
        <span className={styles.brand}>SONNY</span>
        <span className={styles.headerRight}>
          <span className={styles.status} data-problem={status.problem || undefined} data-blink={status.blink || undefined} role="status">
            <span className={styles.dot} aria-hidden="true" />
            {status.text}
          </span>
          {detailsToggle}
        </span>
      </header>

      <section className={styles.session} aria-label="Focus Session">
        <span className={styles.task}>
          {session === null ? "No Focus Session" : `${session.task} / ${formatClock(session.duration_s)} block`}
          {paused && " · Paused"}
        </span>
        <div className={styles.timer} data-scene={s} aria-label="Time remaining">
          {formatClock(remainingS(session, nowS) ?? 0)}
        </div>
        <AttentionStrip timeline={timeline} durationS={session?.duration_s ?? 1} />
      </section>

      <div className={styles.blocks}>
        <AttentionBlock scene={s} episode={state.episode} nowS={nowS} />
        <SonnyBlock phase={state.agent.phase} caption={caption(state)} dimmed={away} />
        <CameraBlock blind={s === "blind"} hot={s === "phone_use"} dimmed={away} />
      </div>
    </div>
  );
}

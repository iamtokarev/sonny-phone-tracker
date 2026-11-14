// The main view, board 02b (spec §7): header and status line, task line, timer, attention strip, and the
// Attention, Sonny and Camera blocks in one row. One screen at 1440x900; Details opens below it.
import { useState, type ReactNode, type Ref } from "react";
import { SonnyBlock } from "../agent/SonnyBlock";
import { CameraBlock, useCameraFeed } from "../camera/CameraBlock";
import { formatClock, remainingS } from "../clock/timer";
import type { Envelope } from "../protocol.gen";
import type { LiveState } from "../stream/feed";
import { AttentionBlock } from "./AttentionBlock";
import { AttentionStrip } from "./AttentionStrip";
import { clearInS } from "./clear";
import { EndedBar } from "./EndedBar";
import { ExpandedView } from "./ExpandedView";
import styles from "./MainView.module.css";
import { buildTimeline, mergeHistory } from "./timeline";
import { caption, scene, statusLine } from "./view";

export interface MainViewProps {
  /** As the page shows it: an ended session until it clears (clear.ts). */
  state: LiveState;
  /** Epoch seconds on Sonny's clock. */
  nowS: number;
  /** The Details toggle, drawn at the right of the header (ticket 08). */
  detailsToggle?: ReactNode;
  /** The camera fills the page below the header (v2 spec §3); the normal view stays mounted, hidden, and its
   *  canvases stop drawing. */
  expanded?: boolean;
  onExpand?: () => void;
  onCollapse?: () => void;
  /** The Camera block's EXPAND button, for the page to focus after a collapse. */
  expandButton?: Ref<HTMLButtonElement>;
}

export function MainView({ state, nowS, detailsToggle, expanded = false, onExpand, onCollapse, expandButton }: MainViewProps) {
  const camera = useCameraFeed(); // one /ws/camera socket for both views
  // The strip's records outlive `recent`'s 500-envelope window (see timeline.ts).
  const [history, setHistory] = useState<Envelope[]>([]);
  // An ended session (v2 spec §4) shows as it was when it ended, its strip whole and its timer at 0:00.
  const ended = state.ended;
  const session = ended?.session ?? state.session;
  const at = ended?.ended_at ?? nowS;
  const merged = mergeHistory(history, state.recent, session);
  if (merged !== history) setHistory(merged);

  const s = scene(state);
  const status = statusLine(state, nowS);
  const away = s === "away";
  const timeline = session === null ? null : buildTimeline(merged, session, at);
  const paused = session !== null && session.paused_since !== null && s !== "ended";

  return (
    <div className={styles.main} data-scene={s}>
      <header className={styles.header}>
        <span className={styles.brand}>Sonny Focus Companion</span>
        <span className={styles.headerRight}>
          <span className={styles.status} data-problem={status.problem || undefined} data-blink={status.blink || undefined} role="status">
            <span className={styles.dot} aria-hidden="true" />
            {status.text}
          </span>
          {detailsToggle}
        </span>
      </header>

      {expanded && (
        <ExpandedView
          session={session}
          scene={s}
          ended={ended}
          episode={state.episode}
          phase={state.agent.phase}
          feed={camera}
          nowS={nowS}
          onCollapse={() => onCollapse?.()}
        />
      )}

      <section className={styles.session} aria-label="Focus Session" hidden={expanded}>
        <span className={styles.task}>
          {session === null ? "No Focus Session" : `${session.task} / ${formatClock(session.duration_s)} block`}
          {paused && " · Paused"}
        </span>
        <div className={styles.timer} data-scene={s} aria-label="Time remaining">
          {formatClock(ended === null ? (remainingS(session, nowS) ?? 0) : 0)}
        </div>
        <AttentionStrip timeline={timeline} durationS={session?.duration_s ?? 1} />
        {ended !== null && <EndedBar ended={ended} clearInS={clearInS(state, nowS) ?? 0} />}
      </section>

      <div className={styles.blocks} hidden={expanded}>
        <AttentionBlock scene={s} episode={state.episode} nowS={nowS} ended={ended} />
        <SonnyBlock phase={state.agent.phase} caption={caption(state)} dimmed={away} active={!expanded} />
        <CameraBlock
          feed={camera}
          blind={s === "blind"}
          hot={s === "phone_use"}
          dimmed={away}
          onExpand={() => onExpand?.()}
          expandButton={expandButton}
          active={!expanded}
        />
      </div>
    </div>
  );
}

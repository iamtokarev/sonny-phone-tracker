// The shell: header, task line and the timer, from the live feed. Ticket 07 builds the rest of board 02b.
import styles from "./App.module.css";
import { formatClock, remainingS } from "./clock/timer";
import type { LiveState } from "./stream/feed";
import { useFeed, useNow } from "./stream/useFeed";

function status(state: LiveState): { text: string; problem: boolean } {
  if (state.connection !== "live") return { text: "Connecting", problem: false };
  if (state.link.down !== null || state.session?.blind) return { text: "Link lost", problem: true };
  if (state.session === null || state.session.time_up_at !== null) return { text: "Standby", problem: false };
  return { text: "All OK", problem: false };
}

export function App() {
  const state = useFeed();
  const now = useNow();
  const session = state.session;
  const running = session !== null && session.time_up_at === null;
  const { text, problem } = status(state);

  return (
    <main className={styles.page}>
      <header className={styles.header}>
        <span className={styles.brand}>SONNY</span>
        <span className={styles.status} data-problem={problem || undefined} role="status">
          <span className={styles.dot} aria-hidden="true" />
          {text}
        </span>
      </header>

      <section className={styles.session}>
        <span className={styles.task}>
          {session === null ? "No Focus Session" : `${session.task} / ${formatClock(session.duration_s)} block`}
        </span>
        <div className={styles.timer} data-running={running || undefined}>
          {formatClock(remainingS(session, now) ?? 0)}
        </div>
      </section>
    </main>
  );
}

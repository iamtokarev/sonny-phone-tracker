// The Details drawer (spec §7): a `[+] DETAILS` toggle for the header and, below the main view, the
// transcript, the health and the recent events. Hidden until opened.
import { useFeed } from "../stream/useFeed";
import styles from "./Details.module.css";
import { health, recentEvents, transcript } from "./select";
import { detailsClick, useDetailsOpen } from "./toggle";

const DRAWER_ID = "details";

/** `covered`: the expanded camera hides the drawer (v2 spec §3), so the toggle shows it closed and a click
 *  opens it (`onOpen` lets the page collapse the camera). */
export function DetailsToggle({ covered = false, onOpen }: { covered?: boolean; onOpen?: () => void }) {
  const [open, flip] = useDetailsOpen();
  const shown = open && !covered;
  const click = () => {
    const does = detailsClick({ open, covered });
    if (does.flip) flip();
    if (does.uncover) onOpen?.();
  };
  return (
    <button type="button" className={styles.toggle} aria-expanded={shown} aria-controls={DRAWER_ID} onClick={click}>
      {shown ? "[–]" : "[+]"} Details
    </button>
  );
}

export function DetailsDrawer({ covered = false }: { covered?: boolean }) {
  const [open] = useDetailsOpen();
  const shown = open && !covered;
  return (
    <section id={DRAWER_ID} className={styles.drawer} aria-label="Details" hidden={!shown}>
      {shown && <DrawerContent />}
    </section>
  );
}

function DrawerContent() {
  const state = useFeed();
  const start = state.session?.started_at ?? null;
  const lines = transcript(state.recent, start);
  const events = recentEvents(state.recent, start);

  return (
    <>
      <div className={styles.transcript}>
        <h2 className={styles.title}>Transcript</h2>
        {lines.length === 0 && <p className={styles.empty}>Nothing said yet.</p>}
        <ol className={styles.list}>
          {lines.map((l) => (
            <li key={l.seq} className={styles.line} data-nudge={l.nudge || undefined}>
              <span>{l.clock}</span>
              <span className={styles.who}>{l.who}</span>
              <span>{l.text}</span>
            </li>
          ))}
        </ol>
      </div>

      <div className={styles.health}>
        <h2 className={styles.title}>Health</h2>
        <dl className={styles.list}>
          {health(state.link).map((r) => (
            <div key={r.label} className={styles.metric}>
              <dt>{r.label}</dt>
              <dd>{r.value}</dd>
            </div>
          ))}
        </dl>
      </div>

      <div className={styles.events}>
        <h2 className={styles.title}>Events</h2>
        {events.length === 0 && <p className={styles.empty}>None yet.</p>}
        <ol className={styles.list}>
          {events.map((e) => (
            <li key={e.seq} className={styles.event}>
              {e.clock} <span className={styles.who}>{e.type}</span>
            </li>
          ))}
        </ol>
      </div>
    </>
  );
}

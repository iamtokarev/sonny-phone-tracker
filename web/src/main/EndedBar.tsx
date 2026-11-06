// The bar under the attention strip once a Focus Session ended (v2 spec §4, board V2Ended):
// SESSION COMPLETE · CLEARING IN 0:NN · [×] CLEAR NOW.
import { formatClock } from "../clock/timer";
import type { EndedView } from "../protocol.gen";
import { clearNow } from "./clear";
import styles from "./EndedBar.module.css";

export function EndedBar({ ended, clearInS }: { ended: EndedView; clearInS: number }) {
  return (
    <div className={styles.bar}>
      <span className={styles.complete}>Session complete</span>
      <span className={styles.countdown}>Clearing in {formatClock(clearInS)}</span>
      <button type="button" className={styles.clear} onClick={() => clearNow(ended)}>
        [×] Clear now
      </button>
    </div>
  );
}

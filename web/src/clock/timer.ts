// The Focus Session timer, ticked locally from the snapshot's raw times (spec §4), the way Sonny's
// FocusClock reads it: elapsed = (now, or paused_since during a Pause) - started_at - paused_total_s,
// held within 0..duration_s; remaining = duration_s - elapsed.
import type { SessionView } from "../protocol.gen";

/** Seconds left in the session at epoch second `nowS`, or null when no session is open. */
export function remainingS(session: SessionView | null, nowS: number): number | null {
  if (session === null) return null;
  const until = session.paused_since ?? nowS;
  const elapsed = Math.min(session.duration_s, Math.max(0, until - session.started_at - session.paused_total_s));
  return session.duration_s - elapsed;
}

/** `m:ss` (or `h:mm:ss`), rounded up so the face reads 0:00 only when time is up. */
export function formatClock(seconds: number): string {
  const total = Math.max(0, Math.ceil(seconds));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = String(total % 60).padStart(2, "0");
  return h > 0 ? `${h}:${String(m).padStart(2, "0")}:${s}` : `${m}:${s}`;
}

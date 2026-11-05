// The attention strip under the timer (spec §7): progress and history in one bar. A segment per Attention
// State (a Blind spell drawn over whichever state it hid) and a mark per Nudge, in session-elapsed seconds,
// so a Pause takes no room, as on the session clock.
//
// The snapshot's `recent` holds the last 500 envelopes, about 8 minutes once `detections` come every second,
// so the page keeps its own history of the few records the strip needs (`mergeHistory`). Opened mid-session,
// the strip starts from what `recent` still holds: the first change's `was` stands for the time before it.
import { remainingS } from "../clock/timer";
import type { Envelope, SessionView } from "../protocol.gen";

export type StripState = "focused" | "phone_use" | "away" | "blind";

export interface Segment {
  state: StripState;
  from: number;
  to: number;
}

export interface Timeline {
  /** Session time now, in seconds (0..duration_s). */
  elapsedS: number;
  segments: Segment[];
  /** Session times of the Nudges. */
  nudges: number[];
}

function isPauseOrResume(e: Envelope): boolean {
  if (e.type !== "tool_call") return false;
  const { name, result } = e.data;
  return (name === "pause" && result.paused === true) || (name === "resume" && result.resumed === true);
}

function kept(e: Envelope): boolean {
  return (
    e.type === "attention" ||
    e.type === "blind.started" ||
    e.type === "blind.ended" ||
    e.type === "nudge" ||
    isPauseOrResume(e)
  );
}

/** The strip's records for `session`: `history` plus what's new in `recent`, older sessions dropped.
 *  Returns `history` itself when nothing changed. */
export function mergeHistory(history: Envelope[], recent: Envelope[], session: SessionView | null): Envelope[] {
  if (session === null) return history.length === 0 ? history : [];
  const start = session.started_at;
  const current = history.filter((e) => e.t >= start);
  const seen = new Set(current.map((e) => e.seq));
  const added = recent.filter((e) => e.t >= start && kept(e) && !seen.has(e.seq));
  if (added.length === 0 && current.length === history.length) return history;
  return [...current, ...added].sort((a, b) => a.seq - b.seq);
}

export function buildTimeline(history: Envelope[], session: SessionView, nowS: number): Timeline {
  const start = session.started_at;
  const records = history.filter((e) => e.t >= start).sort((a, b) => a.t - b.t || a.seq - b.seq);
  const elapsedS = session.duration_s - (remainingS(session, nowS) ?? session.duration_s);

  // Wall time to session time: Pauses (from pause/resume calls, and the one open now) take no time.
  const pauses: [number, number][] = [];
  let pausedAt: number | null = null;
  for (const e of records) {
    if (e.type !== "tool_call" || !isPauseOrResume(e)) continue;
    if (e.data.name === "pause") pausedAt ??= e.t;
    else if (pausedAt !== null) {
      pauses.push([pausedAt, e.t]);
      pausedAt = null;
    }
  }
  if (session.paused_since !== null) pauses.push([session.paused_since, Infinity]);
  const sessionTime = (t: number): number => {
    let paused = 0;
    for (const [a, b] of pauses) paused += Math.max(0, Math.min(t, b) - Math.max(a, start));
    return Math.min(elapsedS, Math.max(0, t - start - paused));
  };

  const firstChange = records.find((e) => e.type === "attention");
  let attention: StripState = firstChange?.type === "attention" ? firstChange.data.was : "focused";
  let blind = false;
  const segments: Segment[] = [];
  const nudges: number[] = [];
  let from = 0;
  let state: StripState = attention;

  const cut = (at: number) => {
    const next: StripState = blind ? "blind" : attention;
    if (next === state) return;
    if (at > from) segments.push({ state, from, to: at });
    from = Math.max(from, at);
    state = next;
  };

  for (const e of records) {
    const at = sessionTime(e.t);
    if (e.type === "attention") attention = e.data.state;
    else if (e.type === "blind.started") blind = true;
    else if (e.type === "blind.ended") blind = false;
    else if (e.type === "nudge") nudges.push(at);
    cut(at);
  }
  if (elapsedS > from) segments.push({ state, from, to: elapsedS });
  return { elapsedS, segments, nudges };
}

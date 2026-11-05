// The Dashboard's picture of Sonny: a snapshot, then each envelope applied to it, following the same rules
// as Sonny's UiFeed (src/sonny/dashboard/feed.py, `_apply`), so the page agrees with the next snapshot.
import type { Envelope, Snapshot } from "../protocol.gen";

/** How many envelopes `recent` keeps, as Sonny's `recent_max` default. */
export const RECENT_MAX = 500;

export type FeedView = Omit<Snapshot, "server_now">;

export function fromSnapshot(snapshot: Snapshot): FeedView {
  const { seq, session, attention, episode, agent, link, recent } = snapshot;
  return { seq, session, attention, episode, agent, link, recent: recent.slice(-RECENT_MAX) };
}

/** The view after one envelope (a new object; the old one is left as it was). */
export function applyEnvelope(view: FeedView, e: Envelope): FeedView {
  const next: FeedView = { ...view, seq: e.seq };
  if (e.type === "health") {
    next.link = e.data;
    return next; // health ticks are not kept in `recent`
  }
  next.recent = [...view.recent.slice(-(RECENT_MAX - 1)), e];
  const s = view.session;

  switch (e.type) {
    case "session.started":
      next.session = {
        task: e.data.task ?? "",
        started_at: e.t,
        duration_s: (e.data.duration_min ?? 0) * 60,
        paused_since: null,
        paused_total_s: 0,
        time_up_at: null,
        blind: null,
      };
      next.attention = "focused";
      next.episode = null;
      break;
    case "session.ended":
      if (e.data.reason === "completed" && s !== null) {
        next.session = { ...s, time_up_at: e.t, blind: null }; // the extend grace: the session stays open
      } else {
        next.session = null;
      }
      next.attention = null;
      next.episode = null;
      break;
    case "tool_call":
      if (s !== null) next.session = applyTool(s, e.t, e.data.name, e.data.result ?? {});
      break;
    case "attention":
      if (s !== null) {
        next.attention = e.data.state;
        if (view.episode !== null && e.data.state !== "focused") next.episode = { ...view.episode, kind: e.data.state };
      }
      break;
    case "episode_opened":
      next.episode = { number: e.data.number, kind: e.data.kind, started_at: e.t, nudges: [] };
      break;
    case "episode_closed":
      next.episode = null;
      break;
    case "nudge":
      if (view.episode !== null && e.data.episode === view.episode.number) {
        next.episode = { ...view.episode, nudges: [...view.episode.nudges, e.data.intensity] };
      }
      break;
    case "blind.started":
      next.link = { ...view.link, blind_spells: view.link.blind_spells + 1 };
      if (s !== null) next.session = { ...s, blind: e.data.reason };
      break;
    case "blind.ended":
      if (s !== null) next.session = { ...s, blind: null };
      break;
    case "reconnect":
      next.link = { ...view.link, reconnects: view.link.reconnects + 1 };
      break;
    case "detections":
      next.link = { ...view.link, detect_ms: e.data.detect_ms };
      break;
    case "llm_call":
      next.link = { ...view.link, llm_latency_s: e.data.latency_s };
      break;
    case "agent.phase":
      next.agent = { phase: e.data.phase };
      break;
  }
  return next;
}

type Session = NonNullable<FeedView["session"]>;

function applyTool(s: Session, t: number, name: string, result: Record<string, unknown>): Session {
  if (name === "pause" && result.paused) return { ...s, paused_since: t };
  if (name === "resume" && result.resumed && s.paused_since !== null) {
    return { ...s, paused_total_s: s.paused_total_s + (t - s.paused_since), paused_since: null };
  }
  if (name === "extend" && typeof result.extended_by_min === "number") {
    const next = { ...s, duration_s: s.duration_s + result.extended_by_min * 60 };
    if (result.continued && s.time_up_at !== null) {
      // As the session clock does: elapsed time stays at the old Duration.
      next.paused_total_s = t - s.started_at - s.duration_s;
      next.time_up_at = null;
    }
    return next;
  }
  return s;
}

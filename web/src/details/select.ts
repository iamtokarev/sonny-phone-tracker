// What the Details drawer shows (spec §7), picked from the feed's state: the transcript from the recent
// envelopes, the link's health, and the recent events by type and time.
import type { Envelope, Health } from "../protocol.gen";

export const TRANSCRIPT_LINES = 6;

export interface TranscriptLine {
  seq: number;
  clock: string;
  who: "Sonny" | "You" | "Nudge";
  text: string;
  /** Nudge rows carry the accent; nothing else in the drawer does. */
  nudge: boolean;
}

const KIND: Record<string, string> = { focused: "Focused", phone_use: "Phone use", away: "Away" };

const capital = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);
const pad = (n: number) => String(n).padStart(2, "0");

/** `+mm:ss` from the session start (`-mm:ss` before it), or the local `hh:mm:ss` without a session. */
export function clockAt(t: number, sessionStart: number | null): string {
  if (sessionStart === null) {
    const d = new Date(t * 1000);
    return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  }
  const off = Math.round(t - sessionStart);
  const abs = Math.abs(off);
  return `${off < 0 ? "-" : "+"}${pad(Math.floor(abs / 60))}:${pad(abs % 60)}`;
}

/** The last 6 lines said (Sonny's, yours) and Nudges given, newest first. */
export function transcript(recent: readonly Envelope[], sessionStart: number | null): TranscriptLine[] {
  const kinds = new Map<number, string>(); // each Distraction Episode's kind as it stood at each moment
  let open: number | null = null;
  const lines: TranscriptLine[] = [];
  const add = (e: Envelope, who: TranscriptLine["who"], text: string) =>
    lines.push({ seq: e.seq, clock: clockAt(e.t, sessionStart), who, text, nudge: who === "Nudge" });

  for (const e of recent) {
    switch (e.type) {
      case "line":
        add(e, "Sonny", e.data.text);
        break;
      case "owner.spoke":
        add(e, "You", e.data.text);
        break;
      case "episode_opened":
        open = e.data.number;
        kinds.set(open, e.data.kind);
        break;
      case "episode_closed":
        open = null;
        break;
      case "attention":
        if (open !== null && e.data.state !== "focused") kinds.set(open, e.data.state);
        break;
      case "nudge": {
        const kind = e.data.episode === null ? undefined : kinds.get(e.data.episode);
        const intensity = capital(e.data.intensity);
        add(e, "Nudge", kind === undefined ? intensity : `${KIND[kind] ?? kind} · ${e.data.intensity}`);
        break;
      }
    }
  }
  return lines.slice(-TRANSCRIPT_LINES).reverse();
}

export interface HealthRow {
  label: string;
  value: string;
}

const NONE = "—";
const fixed = (n: number | null, digits: number) => (n === null ? NONE : n.toFixed(digits));

/** The latest health: link, fps, detect ms, lag, LLM s, reconnects (a dash for what isn't measured yet). */
export function health(link: Health): HealthRow[] {
  return [
    { label: "Link", value: link.down === null ? "OK" : "Lost" },
    { label: "FPS", value: fixed(link.camera_fps, 1) },
    { label: "Detect ms", value: fixed(link.detect_ms, 1) },
    { label: "Lag ms", value: link.stream_lag_s === null ? NONE : fixed(link.stream_lag_s * 1000, 0) },
    { label: "LLM s", value: fixed(link.llm_latency_s, 2) },
    { label: "Reconnects", value: String(link.reconnects) },
  ];
}

export const EVENT_ROWS = 7;

export interface EventRow {
  seq: number;
  clock: string;
  type: string;
}

/** The latest events by type and time, newest first. `detections` comes every second and would push
 * everything else out; its detect ms is in the health. */
export function recentEvents(recent: readonly Envelope[], sessionStart: number | null): EventRow[] {
  const rows: EventRow[] = [];
  for (let i = recent.length - 1; i >= 0 && rows.length < EVENT_ROWS; i--) {
    const e = recent[i];
    if (e.type !== "detections") rows.push({ seq: e.seq, clock: clockAt(e.t, sessionStart), type: e.type });
  }
  return rows;
}

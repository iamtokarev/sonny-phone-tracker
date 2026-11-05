// The main view's state rules (spec §7), read off the live feed.
import type { LiveState } from "../stream/feed";

/** Which of board 02b's states the page is in. */
export type Scene = "resting" | "focused" | "phone_use" | "away" | "blind" | "ended";

export function scene(state: LiveState): Scene {
  const s = state.session;
  if (s === null) return "resting";
  if (s.time_up_at !== null) return "ended";
  if (s.blind !== null) return "blind";
  return state.attention ?? "focused";
}

export const SCENE_WORD: Record<Scene, string> = {
  resting: "Resting",
  focused: "Focused",
  phone_use: "Phone use",
  away: "Away",
  blind: "Blind",
  ended: "Complete",
};

export interface StatusLine {
  text: string;
  problem: boolean;
  blink: boolean;
}

export function statusLine(state: LiveState): StatusLine {
  if (state.connection !== "live") return { text: "Connecting", problem: false, blink: false };
  const s = scene(state);
  if (s === "blind") return { text: "Link lost", problem: true, blink: true };
  if (state.link.down !== null) return { text: "Link lost", problem: true, blink: false };
  if (s === "resting" || s === "ended") return { text: "Standby", problem: false, blink: false };
  return { text: "All OK", problem: false, blink: false };
}

export interface Caption {
  text: string;
  /** Being spoken now. */
  live: boolean;
}

/** Sonny's latest line. A `line` record comes when the line ends, so while a newer line is being spoken the
 *  caption is a bold ellipsis rather than the previous line. */
export function caption(state: LiveState): Caption {
  let line: { t: number; text: string } | null = null;
  let speakingSince: number | null = null;
  for (const e of state.recent) {
    if (e.type === "line" && e.data.text.trim() !== "") line = { t: e.t, text: e.data.text.trim() };
    else if (e.type === "agent.phase") speakingSince = e.data.phase === "speaking" ? e.t : null;
  }
  if (state.agent.phase === "speaking" && (line === null || speakingSince === null || line.t < speakingSince)) {
    return { text: "…", live: true };
  }
  return line === null ? { text: "Nothing said yet.", live: false } : { text: `“${line.text}”`, live: false };
}

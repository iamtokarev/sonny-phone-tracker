// Clearing the page after a Focus Session ends (v2 spec §4). Sonny's snapshot keeps the last session's end
// (`ended`) until another starts; the page shows it, with its summary and a countdown, for `clear_after_s` on
// Sonny's clock, then clears to the resting view. CLEAR NOW clears at once. Clearing is the page's own view
// state and never changes Sonny; the cleared session's `started_at` is remembered in localStorage when the
// browser allows, so a reload does not bring it back.
import { useSyncExternalStore } from "react";
import { formatClock, remainingS } from "../clock/timer";
import type { EndedView } from "../protocol.gen";
import type { FeedView } from "../stream/state";

const KEY = "sonny.cleared.started_at";

type Storage = Pick<globalThis.Storage, "getItem" | "setItem">;
type Ending = Pick<FeedView, "ended" | "clear_after_s">;

/** Seconds until the ended view clears itself at `nowS` (Sonny's clock), or null when none is showing. */
export function clearInS(state: Ending, nowS: number): number | null {
  return state.ended === null ? null : state.ended.ended_at + state.clear_after_s - nowS;
}

/** The state the page shows: `state` itself, or the resting view once its ended session is cleared
 *  (its countdown ran out, or `clearedStartedAt` is that session's start: CLEAR NOW). */
export function afterClearing<T extends FeedView>(state: T, nowS: number, clearedStartedAt: number | null): T {
  const ended = state.ended;
  if (ended === null) return state;
  const left = clearInS(state, nowS) ?? 0;
  if (left > 0 && ended.session.started_at !== clearedStartedAt) return state;
  return { ...state, session: null, ended: null, attention: null, episode: null };
}

/** What the ended view calls the end: the bar's headline and the Attention block's word. Time up
 *  completes a Focus Session; any other end (the owner, Blind, Mac sleep, a long Pause, quitting) ends it. */
export function endedCopy(reason: EndedView["reason"]): { headline: string; word: string } {
  return reason === "completed"
    ? { headline: "Focus Session complete", word: "Complete" }
    : { headline: "Focus Session ended", word: "Ended" };
}

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

/** `<how long it ran> · <N> episodes · <N> nudges`; how long it ran leaves out its Pauses. */
export function endedSummary(ended: EndedView): string {
  const s = ended.session;
  const ran = s.duration_s - (remainingS(s, ended.ended_at) ?? s.duration_s);
  return `${formatClock(ran)} · ${plural(s.episodes, "episode")} · ${plural(s.nudges, "nudge")}`;
}

export interface ClearedSessions {
  /** The `started_at` of the session last cleared with CLEAR NOW, or null. */
  cleared(): number | null;
  clear(ended: EndedView): void;
  subscribe(listener: () => void): () => void;
}

export function clearedSessions(storage: Storage | null): ClearedSessions {
  let cleared: number | null = null;
  try {
    const raw = storage?.getItem(KEY);
    cleared = raw == null || raw === "" || !Number.isFinite(Number(raw)) ? null : Number(raw);
  } catch {
    // private window or blocked site data: nothing cleared yet
  }
  const listeners = new Set<() => void>();
  return {
    cleared: () => cleared,
    clear(ended) {
      cleared = ended.session.started_at;
      try {
        storage?.setItem(KEY, String(cleared));
      } catch {
        // not remembered across reloads, still cleared
      }
      for (const listener of listeners) listener();
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  };
}

function browserStorage(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}

const page = clearedSessions(browserStorage());

/** `state` as the page shows it at `nowS`, after clearing (see `afterClearing`). */
export function useCleared<T extends FeedView>(state: T, nowS: number): T {
  return afterClearing(state, nowS, useSyncExternalStore(page.subscribe, page.cleared));
}

/** CLEAR NOW: clear this ended session at once. */
export const clearNow = (ended: EndedView): void => page.clear(ended);

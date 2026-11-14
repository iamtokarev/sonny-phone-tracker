// The owner's PAUSE FEED choice, remembered in localStorage when the browser allows, so a reload stays paused
// (and connects to /ws/camera already off: see socket.ts).
import { readMigratedPreference } from "../savedPreference";

const KEY = "sonny-focus-companion.camera.feedOff";

type Storage = Pick<globalThis.Storage, "getItem" | "setItem">;

export interface FeedOffChoice {
  read(): boolean;
  save(off: boolean): void;
}

export function feedOffChoice(storage: Storage | null): FeedOffChoice {
  return {
    read() {
      try {
        return readMigratedPreference(storage, KEY, "sonny.camera.feedOff") === "1";
      } catch {
        return false; // private window or blocked site data: the feed starts on
      }
    },
    save(off) {
      try {
        storage?.setItem(KEY, off ? "1" : "0");
      } catch {
        // not remembered, still applied
      }
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

/** The page's own choice, in this browser's storage. */
export const pageFeedOff = feedOffChoice(browserStorage());

// Whether the Details drawer is open: closed by default, remembered in localStorage when the browser allows.
// One store for the page, so the toggle in the header and the drawer below the main view agree.
import { useSyncExternalStore } from "react";

const KEY = "sonny.details.open";

type Storage = Pick<globalThis.Storage, "getItem" | "setItem">;

export interface DetailsToggleStore {
  isOpen(): boolean;
  flip(): void;
  subscribe(listener: () => void): () => void;
}

export function detailsToggle(storage: Storage | null): DetailsToggleStore {
  let open = false;
  try {
    open = storage?.getItem(KEY) === "1";
  } catch {
    // private window or blocked site data: start closed
  }
  const listeners = new Set<() => void>();
  return {
    isOpen: () => open,
    flip() {
      open = !open;
      try {
        storage?.setItem(KEY, open ? "1" : "0");
      } catch {
        // not remembered, still toggled
      }
      for (const listener of listeners) listener();
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  };
}

/** What a click on the toggle does. The drawer shows when it is open and not covered by the expanded camera:
 *  a click then closes it. Otherwise the click shows it: it opens the drawer if closed, and asks the page to
 *  uncover it (collapse the camera). */
export function detailsClick({ open, covered }: { open: boolean; covered: boolean }): { flip: boolean; uncover: boolean } {
  const shown = open && !covered;
  if (shown) return { flip: true, uncover: false };
  return { flip: !open, uncover: true };
}

function browserStorage(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}

const page = detailsToggle(browserStorage());

/** The page's Details state and the way to flip it. */
export function useDetailsOpen(): [open: boolean, flip: () => void] {
  return [useSyncExternalStore(page.subscribe, page.isOpen), page.flip];
}

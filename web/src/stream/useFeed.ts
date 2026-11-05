import { createContext, useContext, useEffect, useState, useSyncExternalStore } from "react";
import type { Feed, LiveState } from "./feed";

/** The page's one feed (created in main.tsx). */
export const FeedContext = createContext<Feed | null>(null);

function useFeedObject(): Feed {
  const feed = useContext(FeedContext);
  if (feed === null) throw new Error("no FeedContext provider");
  return feed;
}

/** Sonny's state as the feed last saw it; re-renders on every change. */
export function useFeed(): LiveState {
  const feed = useFeedObject();
  return useSyncExternalStore(feed.subscribe, feed.getState);
}

/** Epoch seconds on Sonny's clock, re-read every `everyMs` so the time remaining ticks between envelopes. */
export function useNow(everyMs = 250): number {
  const feed = useFeedObject();
  const [now, setNow] = useState(feed.nowS);
  useEffect(() => {
    const id = setInterval(() => setNow(feed.nowS()), everyMs);
    return () => clearInterval(id);
  }, [feed, everyMs]);
  return now;
}

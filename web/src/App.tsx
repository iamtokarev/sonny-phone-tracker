// The Dashboard page: board 02b's main view, and the Details drawer below it. The page decides its layout
// (fills the window, natural height with the drawer open, or the camera expanded) and the blocks follow it
// through CSS tokens.
import { useEffect, useReducer, useRef } from "react";
import styles from "./App.module.css";
import { DetailsDrawer, DetailsToggle } from "./details/Details";
import { useDetailsOpen } from "./details/toggle";
import { useCleared } from "./main/clear";
import { PAGE_VIEW_START, pageLayout, pageView } from "./main/layout";
import { MainView } from "./main/MainView";
import { useFeed, useNow } from "./stream/useFeed";

export function App() {
  const now = useNow();
  const state = useCleared(useFeed(), now); // an ended session shows until the page clears it
  const [detailsOpen] = useDetailsOpen();
  const [view, dispatch] = useReducer(pageView, PAGE_VIEW_START);
  const page = useRef<HTMLElement>(null);
  const wasExpanded = useRef(false);

  // Expanding moves focus to the page so Esc works at once; collapsing hands it to EXPAND when it was lost.
  useEffect(() => {
    const el = page.current;
    if (el === null || view.expanded === wasExpanded.current) return;
    wasExpanded.current = view.expanded;
    if (view.expanded) el.focus();
    else if (document.activeElement === document.body || document.activeElement === el) {
      el.querySelector<HTMLButtonElement>("[data-camera-expand]")?.focus();
    }
  }, [view.expanded]);

  return (
    <main
      ref={page}
      tabIndex={-1}
      className={styles.page}
      data-layout={pageLayout({ detailsOpen, expanded: view.expanded })}
      onKeyDown={(e) => dispatch({ type: "key", key: e.key })}
    >
      <MainView
        state={state}
        nowS={now}
        detailsToggle={<DetailsToggle covered={view.expanded} onOpen={() => dispatch({ type: "detailsOpened" })} />}
        expanded={view.expanded}
        onExpand={() => dispatch({ type: "expand" })}
        onCollapse={() => dispatch({ type: "collapse" })}
      />
      <DetailsDrawer covered={view.expanded} />
    </main>
  );
}

// The Dashboard page: board 02b's main view, and the Details drawer below it. The page decides its layout
// (fills the window, natural height with the drawer open, or the camera expanded; usePageView) and the blocks
// follow it through CSS tokens.
import styles from "./App.module.css";
import { DetailsDrawer, DetailsToggle } from "./details/Details";
import { useDetailsOpen } from "./details/toggle";
import { useCleared } from "./main/clear";
import { MainView } from "./main/MainView";
import { usePageView } from "./main/usePageView";
import { useFeed, useNow } from "./stream/useFeed";

export function App() {
  const now = useNow();
  const state = useCleared(useFeed(), now); // an ended session shows until the page clears it
  const [detailsOpen] = useDetailsOpen();
  const view = usePageView({ detailsOpen });

  return (
    <main {...view.page} className={styles.page} data-layout={view.layout}>
      <MainView
        state={state}
        nowS={now}
        detailsToggle={<DetailsToggle covered={view.expanded} onOpen={view.detailsOpened} />}
        expanded={view.expanded}
        onExpand={view.expand}
        onCollapse={view.collapse}
        expandButton={view.expandButton}
      />
      <DetailsDrawer covered={view.expanded} />
    </main>
  );
}

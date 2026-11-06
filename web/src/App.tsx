// The Dashboard page: board 02b's main view, and the Details drawer below it. The page decides its layout
// (fills the window, or natural height with the drawer open) and the blocks follow it through CSS tokens.
import styles from "./App.module.css";
import { DetailsDrawer, DetailsToggle } from "./details/Details";
import { useDetailsOpen } from "./details/toggle";
import { pageLayout } from "./main/layout";
import { MainView } from "./main/MainView";
import { useFeed, useNow } from "./stream/useFeed";

export function App() {
  const state = useFeed();
  const now = useNow();
  const [detailsOpen] = useDetailsOpen();
  return (
    <main className={styles.page} data-layout={pageLayout({ detailsOpen })}>
      <MainView state={state} nowS={now} detailsToggle={<DetailsToggle />} />
      <DetailsDrawer />
    </main>
  );
}

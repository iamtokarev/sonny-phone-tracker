// The Dashboard page: board 02b's main view, and the Details drawer below it.
import styles from "./App.module.css";
import { DetailsDrawer, DetailsToggle } from "./details/Details";
import { MainView } from "./main/MainView";
import { useFeed, useNow } from "./stream/useFeed";

export function App() {
  const state = useFeed();
  const now = useNow();
  return (
    <main className={styles.page}>
      <MainView state={state} nowS={now} detailsToggle={<DetailsToggle />} />
      <DetailsDrawer />
    </main>
  );
}

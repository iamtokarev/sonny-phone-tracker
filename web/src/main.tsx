import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { connectFeed, rpcUrl } from "./stream/feed";
import { FeedContext } from "./stream/useFeed";
import "./styles/tokens.css";

const root = document.getElementById("root");
if (!root) throw new Error("index.html has no #root");

const feed = connectFeed(rpcUrl());

createRoot(root).render(
  <StrictMode>
    <FeedContext value={feed}>
      <App />
    </FeedContext>
  </StrictMode>,
);

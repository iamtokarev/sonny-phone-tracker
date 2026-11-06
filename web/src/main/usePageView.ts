// The page's own view (v2 spec §2, §3), as App wires it: the view state (layout.ts), the layout it gives with
// Details, Esc on the page, and focus. Expanding moves focus to the page so Esc works at once; collapsing hands
// it to EXPAND when it was lost (the expanded view's COLLAPSE went away under it).
import { useEffect, useReducer, useRef, type KeyboardEvent, type RefObject } from "react";
import { PAGE_VIEW_START, pageLayout, pageView, type Layout } from "./layout";

export interface PageViewControls {
  expanded: boolean;
  layout: Layout;
  /** For the page element: focusable, and Esc collapses. */
  page: { ref: RefObject<HTMLElement | null>; tabIndex: -1; onKeyDown: (e: KeyboardEvent) => void };
  /** For the Camera block's EXPAND button, which gets focus back after a collapse. */
  expandButton: RefObject<HTMLButtonElement | null>;
  expand(): void;
  collapse(): void;
  /** Details was opened: the camera collapses. */
  detailsOpened(): void;
}

export function usePageView({ detailsOpen }: { detailsOpen: boolean }): PageViewControls {
  const [view, dispatch] = useReducer(pageView, PAGE_VIEW_START);
  const page = useRef<HTMLElement>(null);
  const expandButton = useRef<HTMLButtonElement>(null);
  const wasExpanded = useRef(false);

  useEffect(() => {
    const el = page.current;
    if (el === null || view.expanded === wasExpanded.current) return;
    wasExpanded.current = view.expanded;
    if (view.expanded) el.focus();
    else if (document.activeElement === document.body || document.activeElement === el) expandButton.current?.focus();
  }, [view.expanded]);

  return {
    expanded: view.expanded,
    layout: pageLayout({ detailsOpen, expanded: view.expanded }),
    page: { ref: page, tabIndex: -1, onKeyDown: (e) => dispatch({ type: "key", key: e.key }) },
    expandButton,
    expand: () => dispatch({ type: "expand" }),
    collapse: () => dispatch({ type: "collapse" }),
    detailsOpened: () => dispatch({ type: "detailsOpened" }),
  };
}

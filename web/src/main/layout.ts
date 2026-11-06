// How the page is laid out (v2 spec §2, §3). "fill": the page is the tab's height and the blocks row takes the
// rest, with sizes scaled to the window. "natural": v1 sizes, the main view's own height, the drawer below.
// "expanded": header, a compact bar and the camera at full width filling the rest of the window.
export type Layout = "fill" | "natural" | "expanded";

export function pageLayout({ detailsOpen, expanded }: { detailsOpen: boolean; expanded: boolean }): Layout {
  if (expanded) return "expanded";
  return detailsOpen ? "natural" : "fill";
}

/** The page's own view state: whether the camera is expanded. Not remembered across reloads. */
export interface PageView {
  expanded: boolean;
}

export type PageViewAction =
  | { type: "expand" }
  | { type: "collapse" }
  /** A key pressed while the page has focus; Esc collapses. */
  | { type: "key"; key: string }
  /** Details was opened: it lives below the normal view, so the camera collapses. */
  | { type: "detailsOpened" };

export const PAGE_VIEW_START: PageView = { expanded: false };

const NORMAL: PageView = { expanded: false };

export function pageView(view: PageView, action: PageViewAction): PageView {
  switch (action.type) {
    case "expand":
      return view.expanded ? view : { expanded: true };
    case "collapse":
    case "detailsOpened":
      return view.expanded ? NORMAL : view;
    case "key":
      return action.key === "Escape" && view.expanded ? NORMAL : view;
  }
}

// How the page is laid out (v2 spec §2). "fill": the page is the tab's height and the blocks row takes the
// rest, with sizes scaled to the window. "natural": v1 sizes, the main view's own height, the drawer below.
export type Layout = "fill" | "natural";

export function pageLayout({ detailsOpen }: { detailsOpen: boolean }): Layout {
  return detailsOpen ? "natural" : "fill";
}

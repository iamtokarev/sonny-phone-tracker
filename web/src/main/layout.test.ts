import { describe, expect, it } from "vitest";
import { pageLayout, pageView, PAGE_VIEW_START, type PageView } from "./layout";

const expanded: PageView = { expanded: true };
const normal: PageView = { expanded: false };

describe("pageLayout", () => {
  it("fills the window while Details is closed", () => {
    expect(pageLayout({ detailsOpen: false, expanded: false })).toBe("fill");
  });

  it("takes its natural height, the drawer below, while Details is open", () => {
    expect(pageLayout({ detailsOpen: true, expanded: false })).toBe("natural");
  });

  it("shows the expanded camera while it is expanded", () => {
    expect(pageLayout({ detailsOpen: false, expanded: true })).toBe("expanded");
  });
});

describe("pageView", () => {
  it("starts with the camera in its block: expanding is not remembered across reloads", () => {
    expect(PAGE_VIEW_START).toEqual(normal);
  });

  it("expands the camera on EXPAND and returns on COLLAPSE", () => {
    expect(pageView(normal, { type: "expand" })).toEqual(expanded);
    expect(pageView(expanded, { type: "collapse" })).toEqual(normal);
  });

  it("returns to the normal view on Esc while expanded", () => {
    expect(pageView(expanded, { type: "key", key: "Escape" })).toEqual(normal);
  });

  it("ignores Esc in the normal view and other keys while expanded", () => {
    expect(pageView(normal, { type: "key", key: "Escape" })).toBe(normal);
    expect(pageView(expanded, { type: "key", key: "Enter" })).toBe(expanded);
  });

  it("collapses the camera when Details opens", () => {
    expect(pageView(expanded, { type: "detailsOpened" })).toEqual(normal);
    expect(pageView(normal, { type: "detailsOpened" })).toBe(normal);
  });
});

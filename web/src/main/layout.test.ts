import { describe, expect, it } from "vitest";
import { pageLayout } from "./layout";

describe("pageLayout", () => {
  it("fills the window while Details is closed", () => {
    expect(pageLayout({ detailsOpen: false })).toBe("fill");
  });

  it("takes its natural height, the drawer below, while Details is open", () => {
    expect(pageLayout({ detailsOpen: true })).toBe("natural");
  });
});

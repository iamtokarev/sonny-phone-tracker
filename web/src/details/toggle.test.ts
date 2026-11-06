import { describe, expect, it } from "vitest";
import { detailsClick, detailsToggle } from "./toggle";

const memory = (): Pick<Storage, "getItem" | "setItem"> => {
  const items = new Map<string, string>();
  return { getItem: (k) => items.get(k) ?? null, setItem: (k, v) => void items.set(k, v) };
};

describe("the Details toggle", () => {
  it("starts closed, and opens and closes, telling subscribers", () => {
    const toggle = detailsToggle(memory());
    let told = 0;
    toggle.subscribe(() => told++);
    expect(toggle.isOpen()).toBe(false);
    toggle.flip();
    expect(toggle.isOpen()).toBe(true);
    toggle.flip();
    expect(toggle.isOpen()).toBe(false);
    expect(told).toBe(2);
  });

  it("remembers being open for the next page load", () => {
    const storage = memory();
    detailsToggle(storage).flip();
    expect(detailsToggle(storage).isOpen()).toBe(true);
  });

  it("still works when the browser refuses storage", () => {
    const refusing = {
      getItem: () => {
        throw new Error("denied");
      },
      setItem: () => {
        throw new Error("denied");
      },
    };
    const toggle = detailsToggle(refusing);
    expect(toggle.isOpen()).toBe(false);
    toggle.flip();
    expect(toggle.isOpen()).toBe(true);
  });
});

describe("a click on [+] DETAILS", () => {
  it("closes the drawer when it shows, and otherwise shows it: opening it if closed, uncovering it if covered", () => {
    expect(detailsClick({ open: true, covered: false })).toEqual({ flip: true, uncover: false });
    expect(detailsClick({ open: false, covered: false })).toEqual({ flip: true, uncover: true });
    expect(detailsClick({ open: false, covered: true })).toEqual({ flip: true, uncover: true });
    expect(detailsClick({ open: true, covered: true })).toEqual({ flip: false, uncover: true });
  });
});

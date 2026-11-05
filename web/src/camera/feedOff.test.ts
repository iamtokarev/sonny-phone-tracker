import { describe, expect, it } from "vitest";
import { feedOffChoice } from "./feedOff";

const memory = (): Pick<Storage, "getItem" | "setItem"> => {
  const items = new Map<string, string>();
  return { getItem: (k) => items.get(k) ?? null, setItem: (k, v) => void items.set(k, v) };
};

describe("the page's PAUSE FEED choice", () => {
  it("starts with the feed on", () => {
    expect(feedOffChoice(memory()).read()).toBe(false);
  });

  it("is remembered across a reload", () => {
    const storage = memory();
    feedOffChoice(storage).save(true);
    expect(feedOffChoice(storage).read()).toBe(true);
    feedOffChoice(storage).save(false);
    expect(feedOffChoice(storage).read()).toBe(false);
  });

  it("still works when the browser refuses storage", () => {
    const refusing = {
      getItem: () => {
        throw new Error("blocked");
      },
      setItem: () => {
        throw new Error("blocked");
      },
    };
    const choice = feedOffChoice(refusing);
    expect(choice.read()).toBe(false);
    expect(() => choice.save(true)).not.toThrow();
    expect(feedOffChoice(null).read()).toBe(false);
  });
});

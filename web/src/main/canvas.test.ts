import { describe, expect, it } from "vitest";
import { frameLoop, type Frames } from "./canvas";

// A hand-cranked requestAnimationFrame: `tick(ms)` runs whatever is scheduled.
function fakeFrames() {
  let next = 0;
  const queued = new Map<number, FrameRequestCallback>();
  const frames: Frames = {
    request: (cb) => {
      queued.set(++next, cb);
      return next;
    },
    cancel: (id) => void queued.delete(id),
  };
  const tick = (ms: number) => {
    const now = [...queued.values()];
    queued.clear();
    for (const cb of now) cb(ms);
  };
  return { frames, tick, pending: () => queued.size };
}

describe("a canvas's frame loop", () => {
  it("draws on every frame, in seconds, until stopped", () => {
    const { frames, tick, pending } = fakeFrames();
    const drawn: number[] = [];
    const stop = frameLoop(true, (t) => drawn.push(t), frames);
    tick(1000);
    tick(1500);
    stop();
    tick(2000);
    expect(drawn).toEqual([1, 1.5]);
    expect(pending()).toBe(0);
  });

  it("draws nothing while inactive (a hidden canvas)", () => {
    const { frames, tick, pending } = fakeFrames();
    const drawn: number[] = [];
    const stop = frameLoop(false, (t) => drawn.push(t), frames);
    tick(1000);
    expect(drawn).toEqual([]);
    expect(pending()).toBe(0);
    stop();
  });
});

// The page's state rules against Sonny's, on one fixture (tests/fixtures/dashboard_golden.json): the same
// envelopes from the same snapshot must leave the page where DashboardFeed's snapshot says Sonny is.
// tests/test_dashboard_golden.py checks Python produces the fixture's snapshots; this checks TypeScript does.
import { describe, expect, it } from "vitest";
import golden from "../../../tests/fixtures/dashboard_golden.json";
import type { Envelope, Snapshot } from "../protocol.gen";
import { applyEnvelope, fromSnapshot, type FeedView } from "./state";

type Golden = {
  recent_max: number;
  envelopes: Envelope[];
  initial: Omit<Snapshot, "server_now">;
  expected: Record<string, Omit<Snapshot, "server_now">>;
};
const fixture = golden as unknown as Golden;

describe("the page follows the state the way Sonny does", () => {
  it("starts from the fixture's initial snapshot and the snapshot's own recent_max", () => {
    const view = fromSnapshot({ ...fixture.initial, server_now: 0 });
    expect(view).toEqual(fixture.initial);
    expect(view.recent_max).toBe(fixture.recent_max);
  });

  it("matches Sonny's snapshot after each checkpoint envelope", () => {
    let view: FeedView = fromSnapshot({ ...fixture.initial, server_now: 0 });
    const checked: string[] = [];
    for (const e of fixture.envelopes) {
      view = applyEnvelope(view, e);
      const expected = fixture.expected[String(e.seq)];
      if (expected !== undefined) {
        expect(view, `after seq ${e.seq} (${e.type})`).toEqual(expected);
        checked.push(String(e.seq));
      }
    }
    expect(checked).toEqual(Object.keys(fixture.expected).sort((a, b) => Number(a) - Number(b)));
  });
});

import { describe, expect, it } from "vitest";
import { attachHeld, parseHeld } from "./held";

describe("parseHeld (the event crosses a process boundary)", () => {
  it("accepts names and a count", () => {
    expect(parseHeld({ names: ["a.png", "b.txt"], count: 2 })).toEqual({ names: ["a.png", "b.txt"], count: 2 });
  });
  it("accepts the empty 'nothing held' event", () => {
    expect(parseHeld({ names: [], count: 0 })).toEqual({ names: [], count: 0 });
  });
  it("keeps the real count when more files were held than names were sent", () => {
    expect(parseHeld({ names: ["a.png"], count: 40 })).toEqual({ names: ["a.png"], count: 40 });
  });
  it("rejects anything malformed or hostile", () => {
    const bad = [
      null, undefined, 5, "x", [], {},
      { names: "a.png", count: 1 },
      { names: [1, 2], count: 2 },
      { names: ["a"], count: -1 },
      { names: ["a"], count: 1.5 },
      { names: ["a"], count: Number.NaN },
      { names: ["a"], count: 1e9 },
      { names: Array.from({ length: 500 }, () => "a.png"), count: 500 },
      { names: ["a"], count: "1" },
    ];
    for (const b of bad) expect(parseHeld(b)).toBeNull();
  });
  it("truncates over-long names rather than trusting them", () => {
    const parsed = parseHeld({ names: ["x".repeat(1000) + ".png"], count: 1 });
    expect(parsed?.names[0]?.length).toBeLessThanOrEqual(120);
  });
});

describe("attachHeld", () => {
  it("turns hand.held core events into held inputs and ignores everything else", async () => {
    let handler: ((e: { payload: unknown }) => void) | undefined;
    const listen = async (_: string, h: (e: { payload: unknown }) => void) => {
      handler = h;
      return () => {};
    };
    const got: unknown[] = [];
    await attachHeld(listen as never, (i) => got.push(i));
    handler?.({ payload: { event: "hand.held", data: { names: ["a.png"], count: 1 } } });
    handler?.({ payload: { event: "hand.held", data: { names: 5, count: 1 } } });
    handler?.({ payload: { event: "cv.event", data: { names: ["a.png"], count: 1 } } });
    handler?.({ payload: null });
    expect(got).toEqual([{ type: "held", names: ["a.png"], count: 1 }]);
  });
});

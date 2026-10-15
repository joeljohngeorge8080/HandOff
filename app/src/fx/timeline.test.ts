import { describe, expect, it } from "vitest";
import { DROP_MS, GRAB_MS } from "./effects";
import { MAX_ACTIVE, Timeline, parsePlay } from "./timeline";

describe("timeline", () => {
  it("plays an effect over its duration and then forgets it", () => {
    const tl = new Timeline();
    tl.add("grab", 100, 100, 1000);
    expect(tl.active(1000)).toBe(true);
    expect(tl.shapes(1000 + GRAB_MS / 2).length).toBeGreaterThan(0);
    expect(tl.shapes(1000 + GRAB_MS + 1)).toEqual([]);
    expect(tl.active(1000 + GRAB_MS + 1)).toBe(false);
  });
  it("is time-based: a slow frame skips ahead instead of slowing the animation", () => {
    const tl = new Timeline();
    tl.add("drop", 0, 0, 0);
    expect(tl.shapes(DROP_MS * 0.9).length).toBeGreaterThan(0);
    expect(tl.shapes(DROP_MS * 1.5)).toEqual([]);
  });
  it("several effects can overlap but the number is capped", () => {
    const tl = new Timeline();
    for (let i = 0; i < MAX_ACTIVE + 10; i++) tl.add("grab", i, i, 0);
    expect(tl.count()).toBe(MAX_ACTIVE);
  });
  it("a clock that goes backwards never throws or draws garbage", () => {
    const tl = new Timeline();
    tl.add("grab", 0, 0, 1000);
    expect(() => tl.shapes(500)).not.toThrow();
    expect(tl.shapes(500).every((s) => Number.isFinite(s.r))).toBe(true);
  });
});

describe("parsePlay (the event crosses a process boundary)", () => {
  it("accepts a well-formed play request", () => {
    expect(parsePlay({ kind: "grab", x: 10, y: 20 })).toEqual({ kind: "grab", x: 10, y: 20 });
    expect(parsePlay({ kind: "drop", x: 0, y: 0 })).toEqual({ kind: "drop", x: 0, y: 0 });
  });
  it("rejects anything else", () => {
    for (const bad of [null, undefined, 5, "x", {}, { kind: "boom", x: 1, y: 1 }, { kind: "grab", x: "1", y: 1 },
      { kind: "grab", x: Number.NaN, y: 1 }, { kind: "grab", x: 1, y: Infinity }, { kind: "grab", x: 1e9, y: 1 }]) {
      expect(parsePlay(bad)).toBeNull();
    }
  });
});

describe("timeline with its own durations (the shader effects last longer, ADR-064)", () => {
  const long = { grab: 1900, drop: 2900 } as const;
  it("keeps an effect for the durations it was given", () => {
    const tl = new Timeline(long);
    tl.add("drop", 5, 6, 0);
    expect(tl.active(2800)).toBe(true);
    expect(tl.active(2901)).toBe(false);
  });
  it("lists what is running with its age source, oldest first, and forgets the finished", () => {
    const tl = new Timeline(long);
    tl.add("grab", 1, 2, 0);
    tl.add("drop", 3, 4, 1000);
    expect(tl.running(1500)).toEqual([
      { kind: "grab", x: 1, y: 2, start: 0 },
      { kind: "drop", x: 3, y: 4, start: 1000 },
    ]);
    expect(tl.running(2100).map((r) => r.kind)).toEqual(["drop"]);
    expect(tl.running(9999)).toEqual([]);
  });
  it("the default durations are unchanged", () => {
    const tl = new Timeline();
    tl.add("grab", 0, 0, 0);
    expect(tl.active(GRAB_MS - 1)).toBe(true);
    expect(tl.active(GRAB_MS + 1)).toBe(false);
  });
});

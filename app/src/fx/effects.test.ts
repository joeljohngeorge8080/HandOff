import { describe, expect, it } from "vitest";
import { DROP_MS, GRAB_MS, type Shape, dropShapes, grabShapes, track } from "./effects";

const hands = (s: Shape[]) => s.filter((x) => x.kind === "hand");
const photo = (s: Shape[]) => s.find((x) => x.kind === "photo") as Extract<Shape, { kind: "photo" }>;
const visible = (s: Shape[], pose: "open" | "fist") =>
  hands(s).find((h) => h.kind === "hand" && h.pose === pose)?.alpha ?? 0;

describe("track (keyframes with easing)", () => {
  const keys = [[0, 0], [0.5, 10, "inout"], [1, 20]] as const;
  it("holds the first and last value outside the range", () => {
    expect(track(-1, keys)).toBe(0);
    expect(track(2, keys)).toBe(20);
  });
  it("interpolates and honours easing", () => {
    expect(track(0.25, keys)).toBeCloseTo(5);
    expect(track(0.75, keys)).toBeCloseTo(15);
    expect(track(0.5, [[0, 0, "in"], [1, 10]])).toBeCloseTo(2.5);
    expect(track(0.5, [[0, 0, "out"], [1, 10]])).toBeCloseTo(8.75);
  });
  it("a zero-length segment jumps instead of dividing by zero", () => {
    expect(track(0.5, [[0, 0], [0.5, 1], [0.5, 7], [1, 7]])).toBeCloseTo(1);
    expect(Number.isFinite(track(0.5, [[0.5, 1], [0.5, 7]]))).toBe(true);
  });
});

describe("grab: the open hand closes over the photo and lifts it", () => {
  it("starts with an open hand over the photo at its resting size", () => {
    const s = grabShapes(0.1, 100, 200);
    expect(visible(s, "open")).toBeGreaterThan(0.8);
    expect(visible(s, "fist")).toBe(0);
    expect(photo(s)).toMatchObject({ x: 100, y: 150, shadow: 0 });
    expect(photo(s).w).toBeCloseTo(146);
  });
  it("closes into a fist, with the photo shrinking to half and sinking under the hand", () => {
    const s = grabShapes(0.5, 100, 200);
    expect(visible(s, "open")).toBe(0);
    expect(visible(s, "fist")).toBeGreaterThan(0.8);
    expect(photo(s).w).toBeCloseTo(73);
    expect(photo(s).y).toBeCloseTo(272);
    expect(photo(s).shadow).toBeCloseTo(1);
  });
  it("leaves a dashed ghost where the photo was, and a ripple while it lifts", () => {
    expect(grabShapes(0.1, 0, 0).find((x) => x.kind === "ghost")?.alpha).toBe(0);
    expect(grabShapes(0.5, 0, 0).find((x) => x.kind === "ghost")?.alpha).toBeGreaterThan(0.5);
    expect(grabShapes(0.3, 0, 0).some((x) => x.kind === "ring")).toBe(true);
    expect(grabShapes(0.9, 0, 0).some((x) => x.kind === "ring")).toBe(false);
  });
  it("fades everything out by the end", () => {
    for (const s of grabShapes(0.999, 0, 0)) expect(s.alpha).toBeLessThan(0.05);
  });
  it("draws nothing before it begins or once finished", () => {
    for (const t of [-0.1, 1, 1.5]) expect(grabShapes(t, 0, 0)).toEqual([]);
  });
});

describe("drop: the closed hand opens and the photo lands", () => {
  it("starts as a fist carrying a half-size photo", () => {
    const s = dropShapes(0.15, 100, 200);
    expect(visible(s, "fist")).toBeGreaterThan(0.8);
    expect(visible(s, "open")).toBe(0);
    expect(photo(s).w).toBeCloseTo(73);
    expect(photo(s).y).toBeCloseTo(272);
  });
  it("opens, and the photo grows to its landed size in the spot under the hand", () => {
    const s = dropShapes(0.5, 100, 200);
    expect(visible(s, "open")).toBeGreaterThan(0.8);
    expect(visible(s, "fist")).toBe(0);
    expect(photo(s).w).toBeCloseTo(146 * 1.35);
    expect(photo(s).y).toBeCloseTo(210);
    expect(photo(s).shadow).toBeLessThan(0.5);
  });
  it("ripples out only around the landing", () => {
    expect(dropShapes(0.1, 0, 0).some((x) => x.kind === "ring")).toBe(false);
    expect(dropShapes(0.4, 0, 0).some((x) => x.kind === "ring")).toBe(true);
    expect(dropShapes(0.9, 0, 0).some((x) => x.kind === "ring")).toBe(false);
  });
  it("fades everything out by the end and draws nothing once finished", () => {
    for (const s of dropShapes(0.999, 0, 0)) expect(s.alpha).toBeLessThan(0.05);
    expect(dropShapes(1, 0, 0)).toEqual([]);
    expect(dropShapes(2, 0, 0)).toEqual([]);
  });
});

describe("robustness", () => {
  it("every number is finite and every alpha is in range for any progress, including hostile input", () => {
    for (const t of [-1, 0, 0.001, 0.2, 0.25, 0.3, 0.5, 0.79, 0.8, 0.999, 1, 5, Number.NaN, Infinity]) {
      for (const s of [...grabShapes(t, 10, 10), ...dropShapes(t, 10, 10)]) {
        for (const v of Object.values(s)) if (typeof v === "number") expect(Number.isFinite(v)).toBe(true);
        expect(s.alpha).toBeGreaterThanOrEqual(0);
        expect(s.alpha).toBeLessThanOrEqual(1);
        if (s.kind === "ring") expect(s.r).toBeGreaterThan(0);
        if (s.kind === "photo") expect(s.w).toBeGreaterThan(0);
      }
    }
  });
  it("the clips are long enough to read but short enough not to linger", () => {
    expect(GRAB_MS).toBeGreaterThanOrEqual(1000);
    expect(DROP_MS).toBeGreaterThanOrEqual(1000);
    expect(Math.max(GRAB_MS, DROP_MS)).toBeLessThanOrEqual(2000);
  });
});

import { describe, expect, it } from "vitest";
import { DROP_MS, GRAB_MS, type Shape, dropShapes, grabShapes } from "./effects";

const finite = (s: Shape): boolean =>
  [s.x, s.y, s.r, s.alpha].every(Number.isFinite) && (s.kind !== "ring" || Number.isFinite(s.width));

describe("grab: the liquid-glass bubble implodes", () => {
  it("starts at full size around the point", () => {
    const [glow, rim] = grabShapes(0, 100, 200);
    expect(glow).toMatchObject({ kind: "glow", x: 100, y: 200, r: 140 });
    expect(rim).toMatchObject({ kind: "ring", x: 100, y: 200, width: 3 });
    expect(rim?.r).toBeCloseTo(140 * 0.95);
  });
  it("shrinks monotonically, with a snappy ease-in (cubic)", () => {
    const radii = [0, 0.2, 0.4, 0.6, 0.8, 0.95].map((t) => grabShapes(t, 0, 0)[0]?.r ?? 0);
    for (let i = 1; i < radii.length; i++) expect(radii[i]).toBeLessThan(radii[i - 1] as number);
    expect(grabShapes(0.5, 0, 0)[0]?.r).toBeCloseTo(140 * (1 - 0.125));
  });
  it("fades out over the last fifth so it never pops", () => {
    expect(grabShapes(0.8, 0, 0)[0]?.alpha).toBeCloseTo(1);
    expect(grabShapes(0.9, 0, 0)[0]?.alpha).toBeCloseTo(0.5);
    expect(grabShapes(0.999, 0, 0)[0]?.alpha ?? 0).toBeLessThan(0.01);
  });
  it("draws nothing once finished or before it begins", () => {
    expect(grabShapes(1, 0, 0)).toEqual([]);
    expect(grabShapes(1.5, 0, 0)).toEqual([]);
    expect(grabShapes(-0.1, 0, 0)).toEqual([]);
  });
  it("glow stops are the cyan palette, transparent at the edge", () => {
    const glow = grabShapes(0, 0, 0)[0];
    if (glow?.kind !== "glow") throw new Error("expected a glow");
    expect(glow.stops.map((s) => s[0])).toEqual([0, 0.4, 1]);
    expect(glow.stops[0]?.[1]).toEqual([255, 255, 255, 220 / 255]);
    expect(glow.stops[2]?.[1][3]).toBe(0);
  });
});

describe("drop: a burst, then five alternating neon wavelets", () => {
  it("the core burst exists only early and fades as it grows", () => {
    const at = (t: number) => dropShapes(t, 0, 0).filter((s) => s.kind === "glow");
    expect(at(0.01)).toHaveLength(1);
    expect(at(0.39)).toHaveLength(1);
    expect(at(0.41)).toHaveLength(0);
    expect((at(0.2)[0] as Shape).r).toBeGreaterThan((at(0.01)[0] as Shape).r);
    expect((at(0.2)[0] as Shape).alpha).toBeLessThan((at(0.01)[0] as Shape).alpha);
  });
  it("wavelets start one after another, 0.08 apart, and there are at most five", () => {
    const rings = (t: number) => dropShapes(t, 0, 0).filter((s) => s.kind === "ring");
    expect(rings(0.05)).toHaveLength(1);
    expect(rings(0.1)).toHaveLength(2);
    expect(rings(0.5)).toHaveLength(5);
    for (let t = 0; t <= 1; t += 0.01) expect(rings(t).length).toBeLessThanOrEqual(5);
  });
  it("alternates thick (2.5) and thin (1) lines, outermost first", () => {
    const rings = dropShapes(0.5, 0, 0).filter((s) => s.kind === "ring");
    expect(rings.map((r) => (r.kind === "ring" ? r.width : 0))).toEqual([2.5, 1, 2.5, 1, 2.5]);
    const radii = rings.map((r) => r.r);
    expect([...radii].sort((a, b) => b - a)).toEqual(radii);
  });
  it("never grows past the maximum radius and fades as it expands", () => {
    for (let t = 0.02; t < 1; t += 0.02) {
      for (const s of dropShapes(t, 0, 0)) {
        if (s.kind === "ring") expect(s.r).toBeLessThanOrEqual(180);
      }
    }
    const rings = (t: number) => dropShapes(t, 0, 0).filter((s) => s.kind === "ring");
    expect((rings(0.9)[0] as Shape).alpha).toBeLessThan((rings(0.3)[0] as Shape).alpha);
  });
  it("draws nothing once finished", () => {
    expect(dropShapes(1, 0, 0)).toEqual([]);
    expect(dropShapes(2, 0, 0)).toEqual([]);
  });
});

describe("robustness", () => {
  it("every shape is finite and in range for any progress, including hostile input", () => {
    for (const t of [-1, 0, 0.001, 0.25, 0.5, 0.79, 0.8, 0.999, 1, 5, Number.NaN, Infinity]) {
      for (const s of [...grabShapes(t, 10, 10), ...dropShapes(t, 10, 10)]) {
        expect(finite(s)).toBe(true);
        expect(s.alpha).toBeGreaterThanOrEqual(0);
        expect(s.alpha).toBeLessThanOrEqual(1);
        expect(s.r).toBeGreaterThan(0);
      }
    }
  });
  it("durations match the original timing at 60 fps (about 0.6 s and 1.1 s)", () => {
    expect(GRAB_MS).toBeGreaterThanOrEqual(600);
    expect(GRAB_MS).toBeLessThanOrEqual(700);
    expect(DROP_MS).toBeGreaterThanOrEqual(1000);
    expect(DROP_MS).toBeLessThanOrEqual(1200);
  });
});

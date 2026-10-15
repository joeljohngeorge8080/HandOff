import { describe, expect, it } from "vitest";
import {
  GL_DURATION_MS,
  HOLD_FADE_IN_S,
  SHADER_MAX_AGE_S,
  SLOTS,
  frameUniforms,
  holdAmount,
  renderScale,
} from "./liquid";
import { FRAGMENT_SOURCE, VERTEX_SOURCE } from "./shader";
import { MAX_ACTIVE } from "./timeline";

const fx = (kind: "grab" | "drop", x: number, y: number, start: number) => ({ kind, x, y, start });

describe("how many effects the shader holds", () => {
  it("has a slot for every effect the timeline can run", () => {
    expect(SLOTS).toBe(MAX_ACTIVE);
  });
});

describe("frameUniforms: what the shader is told each frame", () => {
  it("an empty timeline leaves every slot inactive and the bead off", () => {
    const u = frameUniforms([], 1000, 1920, 1080);
    expect(u.ripples).toHaveLength(SLOTS * 4);
    for (let i = 0; i < SLOTS; i++) expect(u.ripples[i * 4 + 2]).toBeLessThan(0); // negative age = off
    expect(u.hold[2]).toBe(0);
  });
  it("turns css pixels into the shader's unit square with the origin at the bottom left", () => {
    const u = frameUniforms([fx("drop", 480, 270, 0)], 500, 1920, 1080);
    expect(u.ripples[0]).toBeCloseTo(0.25);
    expect(u.ripples[1]).toBeCloseTo(0.75);
  });
  it("sends the age in seconds, computed from wall-clock time, and the kind (0 grab, 1 drop)", () => {
    const u = frameUniforms([fx("grab", 0, 0, 1000), fx("drop", 0, 0, 1500)], 2000, 100, 100);
    expect(u.ripples[2]).toBeCloseTo(1);
    expect(u.ripples[3]).toBe(0);
    expect(u.ripples[6]).toBeCloseTo(0.5);
    expect(u.ripples[7]).toBe(1);
  });
  it("never sends more than SLOTS effects (the newest win) and ignores a bad clock", () => {
    const many = Array.from({ length: SLOTS + 3 }, (_, i) => fx("drop", i, i, i * 10));
    const u = frameUniforms(many, 500, 100, 100);
    expect(u.ripples).toHaveLength(SLOTS * 4);
    expect(u.ripples[0]).toBeCloseTo(3 / 100); // oldest kept = the (3+1)th of 7
    const before = frameUniforms([fx("grab", 5, 5, 1000)], 500, 100, 100); // clock went backwards
    expect(before.ripples[2]).toBeLessThan(0);
  });
  it("a zero-sized screen cannot divide by zero", () => {
    const u = frameUniforms([fx("grab", 5, 5, 0)], 100, 0, 0);
    expect([...u.ripples, ...u.hold].every(Number.isFinite)).toBe(true);
  });
  it("an effect older than the shader can draw is switched off", () => {
    const u = frameUniforms([fx("drop", 1, 1, 0)], (SHADER_MAX_AGE_S + 0.1) * 1000, 100, 100);
    expect(u.ripples[2]).toBeLessThan(0);
  });
});

describe("the glass bead under the cursor", () => {
  it("grows in, holds, and fades out again", () => {
    expect(holdAmount(0)).toBe(0);
    expect(holdAmount(HOLD_FADE_IN_S)).toBeCloseTo(1);
    expect(holdAmount(1.0)).toBeCloseTo(1);
    expect(holdAmount(GL_DURATION_MS.grab / 1000)).toBe(0);
    expect(holdAmount(-1)).toBe(0);
    expect(holdAmount(Number.NaN)).toBe(0);
    for (let t = 0; t < 2; t += 0.05) {
      const a = holdAmount(t);
      expect(a).toBeGreaterThanOrEqual(0);
      expect(a).toBeLessThanOrEqual(1);
    }
  });
  it("follows a grab, not a drop", () => {
    const grab = frameUniforms([fx("grab", 50, 25, 0)], 800, 100, 100);
    expect(grab.hold[0]).toBeCloseTo(0.5);
    expect(grab.hold[1]).toBeCloseTo(0.75);
    expect(grab.hold[2]).toBeGreaterThan(0.9);
    expect(frameUniforms([fx("drop", 50, 25, 0)], 800, 100, 100).hold[2]).toBe(0);
  });
  it("with several grabs the strongest bead is shown", () => {
    const u = frameUniforms([fx("grab", 10, 10, 0), fx("grab", 90, 90, 700)], 800, 100, 100);
    expect(u.hold[0]).toBeCloseTo(0.1); // the older, fully formed one beats the new, faint one
  });
});

describe("durations", () => {
  it("fit inside what the shader can draw and are longer than the old canvas effects", () => {
    expect(GL_DURATION_MS.grab / 1000).toBeLessThan(SHADER_MAX_AGE_S);
    expect(GL_DURATION_MS.drop / 1000).toBeLessThan(SHADER_MAX_AGE_S);
    expect(GL_DURATION_MS.drop).toBeGreaterThan(GL_DURATION_MS.grab);
  });
});

describe("renderScale: the shader is costly, so big screens are drawn smaller", () => {
  it("leaves small screens at full resolution", () => {
    expect(renderScale(800, 600, 1)).toBe(1);
  });
  it("shrinks big ones so about a million pixels are drawn", () => {
    const s = renderScale(3840, 2160, 1);
    expect(s).toBeLessThan(0.5);
    expect(3840 * 2160 * s * s).toBeLessThanOrEqual(1_050_000);
  });
  it("never draws more pixels than the screen has, and never returns nonsense", () => {
    expect(renderScale(800, 600, 0.5)).toBe(0.5);
    expect(renderScale(800, 600, 3)).toBe(1);
    for (const bad of [0, -5, Number.NaN, Infinity]) {
      const s = renderScale(bad, 1080, 1);
      expect(s).toBeGreaterThan(0);
      expect(s).toBeLessThanOrEqual(1);
    }
  });
});

describe("the shader source", () => {
  it("is WebGL2 (GLSL ES 3.00) with the uniforms the renderer sets", () => {
    for (const src of [VERTEX_SOURCE, FRAGMENT_SOURCE]) expect(src.trimStart().startsWith("#version 300 es")).toBe(true);
    expect(FRAGMENT_SOURCE).toContain("precision highp float");
    expect(FRAGMENT_SOURCE).toMatch(new RegExp(`uniform\\s+vec4\\s+uR\\[${SLOTS}\\]`));
    expect(FRAGMENT_SOURCE).toMatch(/uniform\s+vec3\s+uHold/);
    expect(FRAGMENT_SOURCE).toMatch(/uniform\s+vec2\s+uRes/);
  });
  it("draws light on a transparent overlay: no wallpaper, and a premultiplied result", () => {
    expect(FRAGMENT_SOURCE).not.toContain("sampler2D");
    expect(FRAGMENT_SOURCE).not.toContain("uTime");
    expect(FRAGMENT_SOURCE).toMatch(/fragColor\s*=\s*vec4\(/);
  });
});

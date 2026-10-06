import { describe, expect, it } from "vitest";
import { basename, extensionOf, kindOf, shownChips } from "./fileGlyph";
import { MAX_CHIPS, emergeFrames, inhaleFrames, settleFrames, shakeFrames, stagger } from "./motion";

describe("keyframes", () => {
  it("inhale starts away from the gateway, invisible, and ends inside it, shrunk and gone", () => {
    const f = inhaleFrames(300, 0);
    expect(f[0]).toMatchObject({ opacity: 0, offset: 0 });
    expect(f[0]?.transform).toContain("translate(-300px");
    expect(f.at(-1)).toMatchObject({ opacity: 0, offset: 1 });
    expect(f.at(-1)?.transform).toContain("translate(0px, 0px) scale(0.2)");
  });
  it("emerge is the mirror of inhale: starts inside the gateway, ends far to the left", () => {
    const f = emergeFrames(300, 0);
    expect(f[0]?.transform).toContain("translate(0px, 0px) scale(0.2)");
    expect(f[0]?.opacity).toBe(0);
    expect(f.at(-1)?.transform).toContain("translate(-300px");
    expect(f.at(-1)?.opacity).toBe(1);
  });
  it("offsets are strictly increasing and span 0..1", () => {
    for (const f of [inhaleFrames(100, 5), emergeFrames(100, 5), settleFrames(), shakeFrames()]) {
      const offsets = f.map((k) => k.offset ?? 0);
      expect(offsets[0]).toBe(0);
      expect(offsets.at(-1)).toBe(1);
      expect([...offsets].sort((a, b) => a - b)).toEqual(offsets);
      expect(new Set(offsets).size).toBe(offsets.length);
    }
  });
  it("animates only transform and opacity (compositor-friendly)", () => {
    for (const f of [inhaleFrames(100, 5), emergeFrames(100, 5), settleFrames(), shakeFrames()]) {
      for (const k of f) expect(Object.keys(k).sort()).toEqual(["offset", "opacity", "transform"]);
    }
  });
  it("staggers several files in order and never for one", () => {
    expect(stagger(0, 1)).toBe(0);
    expect([0, 1, 2].map((i) => stagger(i, 3))).toEqual([0, 130, 260]);
  });
});

describe("file glyphs", () => {
  it("reads extensions case-insensitively and merges jpeg into jpg", () => {
    expect(extensionOf("Photo.JPG")).toBe("jpg");
    expect(kindOf("a.JPEG")).toBe("jpg");
    expect(kindOf("a.PDF")).toBe("pdf");
    expect(kindOf("README")).toBe("file");
    expect(kindOf(".txt")).toBe("file");
    expect(kindOf("a.")).toBe("file");
    expect(kindOf("archive.zip")).toBe("file");
  });
  it("takes the last path component on both Windows and Linux", () => {
    expect(basename("C:\\Users\\me\\Desktop\\photo.jpg")).toBe("photo.jpg");
    expect(basename("/home/me/Desktop/photo.jpg")).toBe("photo.jpg");
    expect(basename("/")).toBe("/");
  });
  it("draws a few tokens and summarises the rest", () => {
    expect(shownChips(["a", "b"])).toEqual({ shown: ["a", "b"], extra: 0 });
    const many = Array.from({ length: 10 }, (_, i) => `f${i}.png`);
    const r = shownChips(many);
    expect(r.shown).toHaveLength(MAX_CHIPS);
    expect(r.extra).toBe(10 - MAX_CHIPS);
    expect(shownChips([])).toEqual({ shown: [], extra: 0 });
  });
});

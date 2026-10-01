import { describe, expect, it } from "vitest";
import {
  clickItem,
  emptySelection,
  normalizeBox,
  prune,
  rectSelect,
  type Box,
  type SelectionState,
} from "./selection";

const order = ["a", "b", "c", "d", "e"];
const none = { ctrl: false, shift: false };
const ids = (s: SelectionState) => [...s.selected].sort();

describe("click selection", () => {
  it("selects a single item and replaces the previous selection", () => {
    let s = clickItem(emptySelection, "a", order, none);
    s = clickItem(s, "c", order, none);
    expect(ids(s)).toEqual(["c"]);
  });

  it("toggles items with Ctrl", () => {
    let s = clickItem(emptySelection, "a", order, none);
    s = clickItem(s, "c", order, { ctrl: true, shift: false });
    expect(ids(s)).toEqual(["a", "c"]);
    s = clickItem(s, "a", order, { ctrl: true, shift: false });
    expect(ids(s)).toEqual(["c"]);
  });

  it("selects the range from the anchor with Shift, in either direction", () => {
    let s = clickItem(emptySelection, "b", order, none);
    s = clickItem(s, "d", order, { ctrl: false, shift: true });
    expect(ids(s)).toEqual(["b", "c", "d"]);
    s = clickItem(s, "a", order, { ctrl: false, shift: true });
    expect(ids(s)).toEqual(["a", "b"]);
  });

  it("keeps the anchor fixed across successive Shift clicks", () => {
    let s = clickItem(emptySelection, "b", order, none);
    s = clickItem(s, "e", order, { ctrl: false, shift: true });
    expect(s.anchor).toBe("b");
  });

  it("extends the existing selection with Ctrl+Shift", () => {
    let s = clickItem(emptySelection, "a", order, none);
    s = clickItem(s, "c", order, { ctrl: true, shift: false });
    s = clickItem(s, "e", order, { ctrl: true, shift: true });
    expect(ids(s)).toEqual(["a", "c", "d", "e"]);
  });

  it("treats Shift without an anchor as a plain click", () => {
    const s = clickItem(emptySelection, "c", order, { ctrl: false, shift: true });
    expect(ids(s)).toEqual(["c"]);
  });

  it("ignores ids that are not in the gallery", () => {
    const s = clickItem(emptySelection, "zzz", order, none);
    expect(s).toBe(emptySelection);
  });
});

describe("drag-rectangle selection", () => {
  const rects = new Map<string, Box>([
    ["a", { left: 0, top: 0, right: 10, bottom: 10 }],
    ["b", { left: 20, top: 0, right: 30, bottom: 10 }],
    ["c", { left: 0, top: 20, right: 10, bottom: 30 }],
  ]);

  it("normalizes a box dragged up and to the left", () => {
    expect(normalizeBox(30, 30, 5, 5)).toEqual({ left: 5, top: 5, right: 30, bottom: 30 });
  });

  it("selects every item the box touches", () => {
    const s = rectSelect(emptySelection, { left: 5, top: 5, right: 25, bottom: 8 }, rects, false);
    expect(ids(s)).toEqual(["a", "b"]);
  });

  it("replaces the previous selection unless additive", () => {
    const base = clickItem(emptySelection, "c", order, none);
    const box = { left: 0, top: 0, right: 12, bottom: 12 };
    expect(ids(rectSelect(base, box, rects, false))).toEqual(["a"]);
    expect(ids(rectSelect(base, box, rects, true))).toEqual(["a", "c"]);
  });

  it("clears the selection when the box touches nothing", () => {
    const base = clickItem(emptySelection, "a", order, none);
    const s = rectSelect(base, { left: 100, top: 100, right: 110, bottom: 110 }, rects, false);
    expect(ids(s)).toEqual([]);
  });
});

describe("prune", () => {
  it("drops deleted ids and a stale anchor", () => {
    let s = clickItem(emptySelection, "a", order, none);
    s = clickItem(s, "c", order, { ctrl: true, shift: false });
    const p = prune(s, ["a", "b"]);
    expect(ids(p)).toEqual(["a"]);
    expect(p.anchor).toBeNull();
  });
});

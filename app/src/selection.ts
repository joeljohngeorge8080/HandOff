// Gallery selection model (FR-006): click, Ctrl-click, Shift-click and drag rectangle.
// Pure functions so the rules are unit-tested without a DOM.

export interface SelectionState {
  selected: ReadonlySet<string>;
  anchor: string | null;
}

export const emptySelection: SelectionState = { selected: new Set(), anchor: null };

export interface Modifiers {
  ctrl: boolean;
  shift: boolean;
}

/** `order` is the visible order of file ids; Shift ranges follow it. */
export function clickItem(
  state: SelectionState,
  id: string,
  order: readonly string[],
  mods: Modifiers,
): SelectionState {
  const index = order.indexOf(id);
  if (index < 0) return state;

  if (mods.shift && state.anchor !== null) {
    const anchorIndex = order.indexOf(state.anchor);
    if (anchorIndex >= 0) {
      const [lo, hi] = anchorIndex < index ? [anchorIndex, index] : [index, anchorIndex];
      const range = order.slice(lo, hi + 1);
      // Ctrl+Shift extends the existing selection; plain Shift replaces it.
      const base = mods.ctrl ? state.selected : new Set<string>();
      return { selected: new Set([...base, ...range]), anchor: state.anchor };
    }
  }
  if (mods.ctrl) {
    const next = new Set(state.selected);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    return { selected: next, anchor: id };
  }
  return { selected: new Set([id]), anchor: id };
}

export interface Box {
  left: number;
  top: number;
  right: number;
  bottom: number;
}

export function normalizeBox(x1: number, y1: number, x2: number, y2: number): Box {
  return {
    left: Math.min(x1, x2),
    top: Math.min(y1, y2),
    right: Math.max(x1, x2),
    bottom: Math.max(y1, y2),
  };
}

export function intersects(a: Box, b: Box): boolean {
  return a.left <= b.right && a.right >= b.left && a.top <= b.bottom && a.bottom >= b.top;
}

/**
 * Drag-select: every item whose rect touches `box` is selected. With Ctrl the result is
 * added to `base`; otherwise it replaces it.
 */
export function rectSelect(
  base: SelectionState,
  box: Box,
  rects: ReadonlyMap<string, Box>,
  additive: boolean,
): SelectionState {
  const hit = [...rects].filter(([, r]) => intersects(box, r)).map(([id]) => id);
  const selected = new Set(additive ? [...base.selected, ...hit] : hit);
  return { selected, anchor: hit[0] ?? (additive ? base.anchor : null) };
}

/** Drop ids that no longer exist (after a delete or refresh). */
export function prune(state: SelectionState, existing: readonly string[]): SelectionState {
  const keep = new Set(existing);
  const selected = new Set([...state.selected].filter((id) => keep.has(id)));
  const anchor = state.anchor !== null && keep.has(state.anchor) ? state.anchor : null;
  return { selected, anchor };
}

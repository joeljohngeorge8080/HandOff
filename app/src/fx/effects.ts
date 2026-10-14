// Full-screen effects (ADR-062): pure geometry for the two hand-gesture animations.
//
// Ported from animation/grab_and_drop.py. In the storyboard an open hand closes over a photo and
// lifts it (grab), and later a closed hand carrying the photo opens and lets it land (drop). Here
// each half plays alone around the cursor. `t` is progress 0..1; the functions return what to
// draw, never drawing themselves, so they are testable without a canvas. Out-of-range or
// non-finite progress draws nothing.

export type Rgb = readonly [number, number, number];

export type Shape =
  | { kind: "ring"; x: number; y: number; r: number; alpha: number; width: number; color: Rgb }
  | { kind: "ghost"; x: number; y: number; w: number; h: number; alpha: number }
  | { kind: "photo"; x: number; y: number; w: number; h: number; alpha: number; shadow: number }
  | { kind: "hand"; pose: "open" | "fist"; x: number; y: number; k: number; alpha: number };

export const GRAB_MS = 1300;
export const DROP_MS = 1700;

export const ACCENT: Rgb = [91, 157, 255];
export const PHOTO_W = 146;
export const PHOTO_H = 110;

const HAND_SCALE = 1.15;
const HAND_ALPHA = 0.95;
// Photo offsets from the hand centre, taken from the storyboard (stage 170,215 -> photo 170,165 / 170,287).
const HELD_DY = -50;
const CARRIED_DY = 72;
const CARRIED_SCALE = 0.5;
const LANDED_SCALE = 1.35;
const LANDED_DY = 10;

type Ease = "lin" | "out" | "in" | "inout";
type Key = readonly [number, number, Ease?];

const valid = (t: number): boolean => Number.isFinite(t) && t >= 0 && t < 1;
const clamp01 = (v: number): number => Math.min(1, Math.max(0, v));

function ease(e: Ease | undefined, u: number): number {
  if (e === "out") return 1 - (1 - u) ** 3;
  if (e === "in") return u * u;
  if (e === "inout") return u * u * (3 - 2 * u);
  return u;
}

/** Keyframes with easing, like CSS @keyframes; easing applies to the segment starting at a key. */
export function track(p: number, keys: readonly Key[]): number {
  const first = keys[0] as Key;
  if (p <= first[0]) return first[1];
  for (let i = 0; i < keys.length - 1; i++) {
    const a = keys[i] as Key;
    const b = keys[i + 1] as Key;
    if (p >= a[0] && p <= b[0]) {
      const span = b[0] - a[0];
      const u = span === 0 ? 1 : (p - a[0]) / span;
      return a[1] + (b[1] - a[1]) * ease(a[2], u);
    }
  }
  return (keys[keys.length - 1] as Key)[1];
}

function ripple(u: number, x: number, y: number, maxR: number, grow: number): Shape[] {
  if (u <= 0 || u >= 1) return [];
  return [{ kind: "ring", x, y, r: maxR * (0.4 + grow * u), alpha: clamp01(0.85 * (1 - u)), width: 3, color: ACCENT }];
}

/** The open hand closes into a fist over the photo, which shrinks and lifts off its spot. */
export function grabShapes(t: number, x: number, y: number): Shape[] {
  if (!valid(t)) return [];
  const hand = track(t, [[0, 0], [0.1, HAND_ALPHA], [0.75, HAND_ALPHA], [1, 0]]);
  const fist = track(t, [[0, 0], [0.2, 0], [0.25, 1]]);
  const fistK = track(t, [[0, 1], [0.2, 1], [0.25, 0.92], [0.33, 1]]);
  const lift = track(t, [[0, 0], [0.2, 0, "inout"], [0.33, 1]]);
  const photoA = track(t, [[0, 1], [0.8, 1], [1, 0]]);
  const ghostA = track(t, [[0, 0], [0.22, 0], [0.38, 1], [0.75, 1], [1, 0]]);
  const scale = 1 + (CARRIED_SCALE - 1) * lift;
  const dy = HELD_DY + (CARRIED_DY - HELD_DY) * lift;
  return [
    { kind: "ghost", x, y: y + HELD_DY, w: PHOTO_W, h: PHOTO_H, alpha: ghostA },
    ...ripple((t - 0.2) / 0.3, x, y + HELD_DY, 78, 1.1),
    { kind: "photo", x, y: y + dy, w: PHOTO_W * scale, h: PHOTO_H * scale, alpha: photoA, shadow: lift },
    { kind: "hand", pose: "open", x, y, k: HAND_SCALE, alpha: hand * (1 - fist) },
    { kind: "hand", pose: "fist", x, y, k: HAND_SCALE * fistK, alpha: hand * fist },
  ];
}

/** A closed hand carrying the photo opens; the photo grows and settles with a ripple. */
export function dropShapes(t: number, x: number, y: number): Shape[] {
  if (!valid(t)) return [];
  const hand = track(t, [[0, 0], [0.1, HAND_ALPHA], [0.6, HAND_ALPHA], [1, 0]]);
  const open = track(t, [[0, 0], [0.25, 0], [0.3, 1]]);
  const land = track(t, [[0, 0], [0.25, 0, "out"], [0.37, 1]]);
  const photoA = track(t, [[0, 1], [0.8, 1], [1, 0]]);
  const shadow = track(t, [[0, 1], [0.25, 1], [0.45, 0]]);
  const scale = CARRIED_SCALE + (LANDED_SCALE - CARRIED_SCALE) * land;
  const dy = CARRIED_DY + (LANDED_DY - CARRIED_DY) * land;
  return [
    ...ripple((t - 0.22) / 0.33, x, y + LANDED_DY, 96, 1.2),
    { kind: "photo", x, y: y + dy, w: PHOTO_W * scale, h: PHOTO_H * scale, alpha: photoA * track(t, [[0, 0], [0.08, 1]]), shadow },
    { kind: "hand", pose: "fist", x, y, k: HAND_SCALE, alpha: hand * (1 - open) },
    { kind: "hand", pose: "open", x, y, k: HAND_SCALE, alpha: hand * open },
  ];
}

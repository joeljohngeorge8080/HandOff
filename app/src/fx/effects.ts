// Full-screen effects (ADR-059): pure geometry for the two hand-gesture animations.
//
// Ported from the standalone PyQt demo: a liquid-glass bubble that implodes (grab) and a burst
// followed by five neon wavelets (drop). `t` is progress 0..1; the functions return what to draw,
// never drawing themselves, so they are testable without a canvas. Out-of-range or non-finite
// progress draws nothing.

export type Rgba = readonly [number, number, number, number];

export type Shape =
  | { kind: "glow"; x: number; y: number; r: number; alpha: number; stops: readonly (readonly [number, Rgba])[] }
  | { kind: "ring"; x: number; y: number; r: number; alpha: number; width: number; color: readonly [number, number, number] };

/** About 40 frames at 60 fps in the original. */
export const GRAB_MS = 650;
/** About 66 frames at 60 fps in the original. */
export const DROP_MS = 1100;

const GRAB_RADIUS = 140;
const BURST_RADIUS = 60;
const WAVE_RADIUS = 180;
const WAVELETS = 5;

const valid = (t: number): boolean => Number.isFinite(t) && t >= 0 && t < 1;
const clamp01 = (v: number): number => Math.min(1, Math.max(0, v));
const rgba = (r: number, g: number, b: number, a: number): Rgba => [r, g, b, clamp01(a)];

/** The bubble implodes (cubic ease-in) with a bright core, a cyan mid and a shrinking glass rim. */
export function grabShapes(t: number, x: number, y: number): Shape[] {
  if (!valid(t)) return [];
  const r = GRAB_RADIUS * (1 - t ** 3);
  if (r <= 0.5) return [];
  const fade = t < 0.8 ? 1 : clamp01((1 - t) / 0.2); // no harsh pop at the end
  return [
    {
      kind: "glow",
      x,
      y,
      r,
      alpha: fade,
      stops: [
        [0, rgba(255, 255, 255, (220 / 255) * fade)],
        [0.4, rgba(0, 220, 255, (150 / 255) * fade)],
        [1, rgba(0, 150, 255, 0)],
      ],
    },
    {
      kind: "ring",
      x,
      y,
      r: r * 0.95,
      alpha: clamp01((1 - t) * fade),
      width: 3,
      color: [200, 255, 255],
    },
  ];
}

/** A fast energy burst, then five expanding wavelets alternating thick and thin. */
export function dropShapes(t: number, x: number, y: number): Shape[] {
  if (!valid(t)) return [];
  const out: Shape[] = [];
  if (t < 0.4) {
    const b = t / 0.4;
    const fade = 1 - b;
    out.push({
      kind: "glow",
      x,
      y,
      r: BURST_RADIUS * (1 + b),
      alpha: clamp01(fade),
      stops: [
        [0, rgba(150, 255, 255, fade)],
        [1, rgba(0, 200, 255, 0)],
      ],
    });
  }
  // Outermost (oldest) wavelet first.
  for (let i = 0; i < WAVELETS; i++) {
    const delay = i * 0.08;
    const local = t - delay;
    if (local <= 0 || local >= 1 - delay) continue;
    const n = local / (1 - delay);
    const r = WAVE_RADIUS * (1 - (1 - n) ** 2); // ease-out: slows as it spreads
    if (r <= 0.5) continue;
    out.push({
      kind: "ring",
      x,
      y,
      r,
      alpha: clamp01(1 - n),
      width: i % 2 === 0 ? 2.5 : 1,
      color: [0, 240, 255],
    });
  }
  return out;
}

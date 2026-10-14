// The set of effects currently playing, advanced by wall-clock time (not frame count).
import { DROP_MS, GRAB_MS, type Shape, dropShapes, grabShapes } from "./effects";

export type FxKind = "grab" | "drop";
export interface PlayRequest {
  kind: FxKind;
  x: number;
  y: number;
}

/** Bounds the work if events arrive faster than they finish. */
export const MAX_ACTIVE = 4;
const MAX_COORD = 100_000;

interface Running {
  kind: FxKind;
  x: number;
  y: number;
  start: number;
}

const DURATION: Record<FxKind, number> = { grab: GRAB_MS, drop: DROP_MS };

export class Timeline {
  private running: Running[] = [];

  add(kind: FxKind, x: number, y: number, now: number): void {
    this.running.push({ kind, x, y, start: now });
    if (this.running.length > MAX_ACTIVE) this.running.splice(0, this.running.length - MAX_ACTIVE);
  }

  count(): number {
    return this.running.length;
  }

  active(now: number): boolean {
    this.prune(now);
    return this.running.length > 0;
  }

  /** Everything to draw at `now`; finished effects are dropped. */
  shapes(now: number): Shape[] {
    this.prune(now);
    return this.running.flatMap((e) => {
      const t = Math.max(0, (now - e.start) / DURATION[e.kind]);
      return e.kind === "grab" ? grabShapes(t, e.x, e.y) : dropShapes(t, e.x, e.y);
    });
  }

  private prune(now: number): void {
    this.running = this.running.filter((e) => now - e.start < DURATION[e.kind]);
  }
}

/** The payload comes from the Rust side; check it anyway. */
export function parsePlay(payload: unknown): PlayRequest | null {
  if (typeof payload !== "object" || payload === null) return null;
  const p = payload as Record<string, unknown>;
  if (p.kind !== "grab" && p.kind !== "drop") return null;
  const ok = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v) && Math.abs(v) <= MAX_COORD;
  if (!ok(p.x) || !ok(p.y)) return null;
  return { kind: p.kind, x: p.x, y: p.y };
}

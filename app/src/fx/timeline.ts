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

export interface RunningFx {
  kind: FxKind;
  x: number;
  y: number;
  start: number;
}

export type Durations = Readonly<Record<FxKind, number>>;

const CANVAS_DURATION: Durations = { grab: GRAB_MS, drop: DROP_MS };

export class Timeline {
  private running_: RunningFx[] = [];

  /** `durations` differ per renderer: the shader effects (ADR-064) last longer than the canvas ones. */
  constructor(private readonly durations: Durations = CANVAS_DURATION) {}

  add(kind: FxKind, x: number, y: number, now: number): void {
    this.running_.push({ kind, x, y, start: now });
    if (this.running_.length > MAX_ACTIVE) this.running_.splice(0, this.running_.length - MAX_ACTIVE);
  }

  count(): number {
    return this.running_.length;
  }

  active(now: number): boolean {
    this.prune(now);
    return this.running_.length > 0;
  }

  /** What is playing at `now`, oldest first; finished effects are dropped. */
  running(now: number): RunningFx[] {
    this.prune(now);
    return this.running_.map((e) => ({ ...e }));
  }

  /** Everything to draw at `now`; finished effects are dropped. */
  shapes(now: number): Shape[] {
    this.prune(now);
    return this.running_.flatMap((e) => {
      const t = Math.max(0, (now - e.start) / this.durations[e.kind]);
      return e.kind === "grab" ? grabShapes(t, e.x, e.y) : dropShapes(t, e.x, e.y);
    });
  }

  private prune(now: number): void {
    this.running_ = this.running_.filter((e) => now - e.start < this.durations[e.kind]);
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

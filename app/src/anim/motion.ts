// Animation primitives for the edge. Timing and geometry are pure functions (unit-tested);
// `Motion` applies them with the Web Animations API, which runs on the compositor and uses only
// transform/opacity. Nothing here runs a frame loop, so an idle edge costs nothing.

export const DURATIONS = {
  inhale: 780,
  emerge: 820,
  settle: 520,
  shake: 360,
  burst: 700,
  squeeze: 480,
  open: 560,
} as const;

export type Keyframe2D = Keyframe & { transform: string; opacity: number };

/** A file token drawn into the gateway: starts `distance` px left, shrinks as it enters. */
export function inhaleFrames(distance: number, lift: number): Keyframe2D[] {
  return [
    { transform: `translate(${-distance}px, ${lift}px) scale(1) rotate(-4deg)`, opacity: 0, offset: 0 },
    { transform: `translate(${-distance * 0.82}px, ${lift * 0.8}px) scale(1) rotate(-3deg)`, opacity: 1, offset: 0.14 },
    { transform: `translate(${-distance * 0.25}px, ${lift * 0.15}px) scale(0.92) rotate(0deg)`, opacity: 1, offset: 0.72 },
    { transform: "translate(0px, 0px) scale(0.2) rotate(0deg)", opacity: 0, offset: 1 },
  ];
}

/** The mirror image: a token leaves the gateway and travels `distance` px to the left. */
export function emergeFrames(distance: number, drop: number): Keyframe2D[] {
  return [
    { transform: "translate(0px, 0px) scale(0.2) rotate(0deg)", opacity: 0, offset: 0 },
    { transform: `translate(${-distance * 0.12}px, 0px) scale(0.92) rotate(0deg)`, opacity: 1, offset: 0.2 },
    { transform: `translate(${-distance * 0.75}px, ${drop * 0.5}px) scale(1) rotate(3deg)`, opacity: 1, offset: 0.78 },
    { transform: `translate(${-distance}px, ${drop}px) scale(1) rotate(0deg)`, opacity: 1, offset: 1 },
  ];
}

export function settleFrames(): Keyframe2D[] {
  return [
    { transform: "scale(1)", opacity: 1, offset: 0 },
    { transform: "scale(1.06)", opacity: 1, offset: 0.4 },
    { transform: "scale(0.9)", opacity: 0, offset: 1 },
  ];
}

/** Palm closing: the gateway pinches in, then springs back to rest. */
export function squeezeFrames(): Keyframe2D[] {
  return [
    { transform: "scale(1)", opacity: 1, offset: 0 },
    { transform: "scale(0.72)", opacity: 1, offset: 0.45 },
    { transform: "scale(1)", opacity: 1, offset: 1 },
  ];
}

/** Palm opening: the gateway swells out, then settles back to rest. */
export function openFrames(): Keyframe2D[] {
  return [
    { transform: "scale(1)", opacity: 1, offset: 0 },
    { transform: "scale(1.28)", opacity: 1, offset: 0.4 },
    { transform: "scale(1)", opacity: 1, offset: 1 },
  ];
}

export function shakeFrames(): Keyframe2D[] {
  return [0, 1, 2, 3, 4].map((i, _n, all) => ({
    transform: `translateX(${[0, -6, 5, -3, 0][i]}px)`,
    opacity: 1,
    offset: i / (all.length - 1),
  }));
}

/** Stagger so several files arrive one after another rather than as one blob. */
export function stagger(index: number, count: number, total = 260): number {
  if (count <= 1) return 0;
  return Math.round((index / (count - 1)) * total);
}

/** At most this many tokens are drawn; the rest are summarised in a +N badge. */
export const MAX_CHIPS = 3;

export function prefersReducedMotion(): boolean {
  return typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;
}

export class Motion {
  private running = new Set<Animation>();

  constructor(private readonly reduced: () => boolean = prefersReducedMotion) {}

  private play(el: HTMLElement, frames: Keyframe2D[], duration: number, delay = 0, fill: FillMode = "forwards"): Animation | null {
    if (this.reduced()) {
      // Reduced motion: no travel, just a quick fade so the state is still perceivable.
      const a = el.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 160, fill: "forwards" });
      this.track(a);
      return a;
    }
    const a = el.animate(frames, { duration, delay, easing: "cubic-bezier(.22,.8,.24,1)", fill });
    this.track(a);
    return a;
  }

  private track(a: Animation): void {
    this.running.add(a);
    const done = () => this.running.delete(a);
    a.addEventListener("finish", done);
    a.addEventListener("cancel", done);
  }

  inhale(chips: HTMLElement[], distance: number): void {
    chips.forEach((c, i) => this.play(c, inhaleFrames(distance, (i - 1) * 14), DURATIONS.inhale, stagger(i, chips.length)));
  }

  emerge(chips: HTMLElement[], distance: number): void {
    chips.forEach((c, i) => this.play(c, emergeFrames(distance, (i - 1) * 26), DURATIONS.emerge, stagger(i, chips.length)));
  }

  settle(chips: HTMLElement[]): void {
    chips.forEach((c) => this.play(c, settleFrames(), DURATIONS.settle));
  }

  shake(el: HTMLElement): void {
    this.play(el, shakeFrames(), DURATIONS.shake, 0, "none");
  }

  squeeze(el: HTMLElement): void {
    this.play(el, squeezeFrames(), DURATIONS.squeeze, 0, "none");
  }

  open(el: HTMLElement): void {
    this.play(el, openFrames(), DURATIONS.open, 0, "none");
  }

  /** Run `fn` once whatever is animating has finished (immediately if nothing is). */
  afterRunning(fn: () => void): void {
    const pending = [...this.running].map((a) => a.finished.catch(() => undefined));
    if (pending.length === 0) fn();
    else void Promise.all(pending).then(fn);
  }

  /** Stop everything at once (a failure must never leave a "sending" animation running). */
  cancelAll(): void {
    for (const a of [...this.running]) a.cancel();
    this.running.clear();
  }
}

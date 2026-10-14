// Renders the machine's view onto the DOM and triggers the matching animations.
//
// The view reads only the model (plus the names of the files being moved, for the tokens). It
// never decides anything: what is shown is a pure function of the state, and a state is only
// ever entered because the machine (and ultimately the core) said so.
import { createChip, shownChips } from "../anim/fileGlyph";
import { Motion } from "../anim/motion";
import { el } from "../dom";
import { type Model, type View, type ViewKind, labelFor } from "./machine";

export type Tone = "neutral" | "ready" | "ok" | "bad" | "busy";

const TONE: Record<ViewKind, Tone> = {
  idle: "neutral",
  approach: "neutral",
  armed: "neutral",
  handle: "neutral",
  validating: "neutral",
  ready: "ready",
  rejected: "bad",
  sending: "busy",
  send_success: "ok",
  send_failed: "bad",
  receiving: "busy",
  receive_success: "ok",
  receive_failed: "bad",
  panel: "neutral",
  hand_grab: "ready",
  hand_release: "busy",
};

const WIDE: ReadonlySet<ViewKind> = new Set([
  "sending",
  "send_success",
  "send_failed",
  "receiving",
  "receive_success",
  "receive_failed",
]);

/** Whether the label sits beside the gateway (the wide animation stage) or under it. */
export function isWide(v: View): boolean {
  return WIDE.has(v.kind) || (v.kind === "rejected" && v.released);
}

export function toneOf(v: View): Tone {
  return TONE[v.kind];
}

export function progressOf(v: View): number {
  if (v.kind === "sending" || v.kind === "receiving") return v.progress;
  if (v.kind === "send_success" || v.kind === "receive_success") return 1;
  return 0;
}

export class EdgeView {
  private readonly under = el("div", { class: "label under" });
  private readonly pill = el("div", { class: "label pill" });
  private readonly lane = el("div", { class: "lane" });
  private readonly gateway = el("div", { class: "gateway" });
  private readonly ripple = el("div", { class: "ripple" });
  private readonly motion = new Motion();
  private names: string[] = [];
  private chips: HTMLElement[] = [];
  private pullFrame = 0;

  constructor(private readonly root: HTMLElement) {
    this.gateway.innerHTML = `
      <svg class="ring" viewBox="0 0 64 64" aria-hidden="true">
        <circle class="track" cx="32" cy="32" r="28"></circle>
        <circle class="fill" cx="32" cy="32" r="28" pathLength="100"></circle>
      </svg>
      <div class="core"></div>
      <svg class="mark" viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7.5"></path></svg>
      <svg class="cross" viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"></path></svg>`;
    root.replaceChildren(
      el("div", { class: "rail" }),
      el("div", { class: "glass" }),
      this.lane,
      this.ripple,
      this.gateway,
      this.under,
      this.pill,
    );
    root.dataset.view = "idle";
    root.dataset.tone = "neutral";
  }

  /** The names of the files in the current drop / transfer, for the tokens. */
  setNames(names: readonly string[]): void {
    this.names = [...names];
  }

  /** Magnetic pull: tilt the gateway toward the pointer while a drag is over the strip. */
  pull(x: number, y: number): void {
    if (this.pullFrame) return;
    this.pullFrame = requestAnimationFrame(() => {
      this.pullFrame = 0;
      this.root.style.setProperty("--pull-y", `${((y - 0.5) * 36).toFixed(1)}px`);
      this.root.style.setProperty("--pull-x", `${(-(1 - x) * 8).toFixed(1)}px`);
    });
  }

  render(model: Model, previous: Model): void {
    const v = model.view;
    const was = previous.view;
    const root = this.root;
    root.dataset.view = v.kind;
    root.dataset.tone = toneOf(v);
    root.dataset.wide = isWide(v) ? "1" : "0";
    root.style.setProperty("--p", progressOf(v).toFixed(3));

    const text = labelFor(v, model.peer);
    this.under.textContent = isWide(v) ? "" : text;
    this.pill.textContent = isWide(v) ? text : "";
    root.setAttribute("aria-label", text || "HandOff");

    if (v.kind === was.kind) return;
    this.onEnter(v, was);
  }

  private onEnter(v: View, was: View): void {
    const distance = Math.max(160, this.root.clientWidth - 110);
    switch (v.kind) {
      case "sending":
        if (was.kind !== "sending") {
          this.motion.cancelAll();
          this.spawnChips();
          this.motion.inhale(this.chips, distance);
          this.burst();
        }
        break;
      case "receiving":
        if (was.kind !== "receiving") {
          this.motion.cancelAll();
          this.spawnChips();
          this.motion.emerge(this.chips, distance);
          this.burst();
        }
        break;
      case "send_success":
      case "receive_success":
        // The result is already on screen; the flourish waits for any token still in flight so
        // a fast transfer does not snap mid-motion.
        this.motion.afterRunning(() => {
          if (this.root.dataset.view !== v.kind) return;
          this.burst();
          this.motion.settle(this.chips);
        });
        break;
      case "hand_grab":
        // Palm closing: the gateway pinches in and a ripple draws inward.
        this.motion.squeeze(this.gateway);
        this.burst();
        break;
      case "hand_release":
        // Palm opening: the gateway swells and a ripple goes outward.
        this.motion.open(this.gateway);
        this.burst();
        break;
      case "rejected":
        this.motion.shake(this.gateway);
        break;
      case "send_failed":
      case "receive_failed":
        // A failed transfer must never leave a "sending" animation running.
        this.motion.cancelAll();
        this.motion.shake(this.gateway);
        this.clearChips();
        break;
      case "idle":
        this.motion.cancelAll();
        this.clearChips();
        this.root.style.removeProperty("--pull-x");
        this.root.style.removeProperty("--pull-y");
        break;
      default:
        break;
    }
  }

  private spawnChips(): void {
    this.clearChips();
    const { shown, extra } = shownChips(this.names.length ? this.names : ["file"]);
    this.chips = shown.map((n, i) => createChip(n, i === shown.length - 1 ? extra : 0));
    this.lane.replaceChildren(...this.chips);
  }

  private clearChips(): void {
    this.chips = [];
    this.lane.replaceChildren();
  }

  private burst(): void {
    // Restart the ripple's CSS animation (it runs once, on the compositor).
    this.ripple.classList.remove("go");
    void this.ripple.offsetWidth;
    this.ripple.classList.add("go");
  }
}

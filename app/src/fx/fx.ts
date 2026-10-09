// The full-screen effects overlay page (ADR-059, effects: ADR-062). Click-through and drawn on a canvas; the Rust
// side shows this window only while an effect plays and tells it where (the cursor) and what.
// It takes no input and talks to nobody: it just listens for `fx-play`.
import { listen } from "@tauri-apps/api/event";
import { drawShape } from "./art";
import { Timeline, parsePlay } from "./timeline";

const canvas = document.getElementById("fx") as HTMLCanvasElement;
const ctx = canvas.getContext("2d");
const timeline = new Timeline();
let frame = 0;

const reduced = (): boolean =>
  typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;

function resize(): void {
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, Math.round(window.innerWidth * dpr));
  canvas.height = Math.max(1, Math.round(window.innerHeight * dpr));
}

function tick(now: number): void {
  frame = 0;
  if (!ctx) return;
  const dpr = window.devicePixelRatio || 1;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, canvas.width / dpr, canvas.height / dpr);
  const shapes = timeline.shapes(now);
  for (const s of shapes) drawShape(ctx, s);
  if (timeline.active(now)) frame = requestAnimationFrame(tick); // no loop while nothing plays
}

resize();
window.addEventListener("resize", resize);
void listen<unknown>("fx-play", ({ payload }) => {
  const play = parsePlay(payload);
  if (!play || reduced()) return;
  timeline.add(play.kind, play.x, play.y, performance.now());
  if (!frame) frame = requestAnimationFrame(tick);
});

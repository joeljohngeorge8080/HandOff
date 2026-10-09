// The full-screen effects overlay page (ADR-059). Click-through and drawn on a canvas; the Rust
// side shows this window only while an effect plays and tells it where (the cursor) and what.
// It takes no input and talks to nobody: it just listens for `fx-play`.
import { listen } from "@tauri-apps/api/event";
import type { Shape } from "./effects";
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

const color = (c: readonly number[], a: number): string => `rgba(${c[0]}, ${c[1]}, ${c[2]}, ${a})`;

function draw(shape: Shape): void {
  if (!ctx) return;
  if (shape.kind === "glow") {
    const g = ctx.createRadialGradient(shape.x, shape.y, 0, shape.x, shape.y, shape.r);
    for (const [offset, c] of shape.stops) g.addColorStop(offset, color(c, c[3]));
    ctx.fillStyle = g;
    ctx.beginPath();
    ctx.arc(shape.x, shape.y, shape.r, 0, Math.PI * 2);
    ctx.fill();
  } else {
    ctx.strokeStyle = color(shape.color, shape.alpha);
    ctx.lineWidth = shape.width;
    ctx.beginPath();
    ctx.arc(shape.x, shape.y, shape.r, 0, Math.PI * 2);
    ctx.stroke();
  }
}

function tick(now: number): void {
  frame = 0;
  if (!ctx) return;
  const dpr = window.devicePixelRatio || 1;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, canvas.width / dpr, canvas.height / dpr);
  const shapes = timeline.shapes(now);
  for (const s of shapes) draw(s);
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

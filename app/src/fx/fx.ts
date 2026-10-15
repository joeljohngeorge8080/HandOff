// The full-screen effects overlay page (ADR-059). Click-through and drawn on a canvas; the Rust
// side shows this window only while an effect plays and tells it where (the cursor) and what.
// It takes no input and talks to nobody: it just listens for `fx-play`.
import { listen } from "@tauri-apps/api/event";
import type { Shape } from "./effects";
import { GL_DURATION_MS, LiquidGlass } from "./liquid";
import { Timeline, parsePlay } from "./timeline";

const canvas = document.getElementById("fx") as HTMLCanvasElement;
const glCanvas = document.getElementById("gl") as HTMLCanvasElement;
const ctx = canvas.getContext("2d");
// The shader effects last longer than the canvas ones, so each renderer has its own timeline.
const canvasTimeline = new Timeline();
const glTimeline = new Timeline(GL_DURATION_MS);
const liquid = LiquidGlass.create(glCanvas); // null: no WebGL2, so the 2D effects play (ADR-064)
let frame = 0;

const reduced = (): boolean =>
  typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;

const useShader = (): boolean => liquid !== null && !liquid.lost;

function resize(): void {
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, Math.round(window.innerWidth * dpr));
  canvas.height = Math.max(1, Math.round(window.innerHeight * dpr));
  liquid?.resize(window.innerWidth, window.innerHeight, dpr);
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
  if (useShader() && liquid) {
    const running = glTimeline.running(now);
    liquid.draw(running, now, window.innerWidth, window.innerHeight);
    if (running.length > 0) frame = requestAnimationFrame(tick);
    else liquid.clear(); // nothing plays: leave the overlay fully transparent
    return;
  }
  if (!ctx) return;
  const dpr = window.devicePixelRatio || 1;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, canvas.width / dpr, canvas.height / dpr);
  const shapes = canvasTimeline.shapes(now);
  for (const s of shapes) draw(s);
  if (canvasTimeline.active(now)) frame = requestAnimationFrame(tick); // no loop while nothing plays
}

function show(shader: boolean): void {
  glCanvas.style.display = shader ? "block" : "none";
  canvas.style.display = shader ? "none" : "block";
}

resize();
window.addEventListener("resize", resize);
void listen<unknown>("fx-play", ({ payload }) => {
  const play = parsePlay(payload);
  if (!play || reduced()) return;
  const shader = useShader();
  show(shader);
  resize(); // the window was just resized from its parked size to the screen
  (shader ? glTimeline : canvasTimeline).add(play.kind, play.x, play.y, performance.now());
  if (!frame) frame = requestAnimationFrame(tick);
});

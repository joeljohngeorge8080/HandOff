// The liquid-glass effects renderer (ADR-064): WebGL2 on top of the pure helpers below.
//
// The helpers turn the timeline into what the shader needs (ages in seconds computed from
// wall-clock time, positions in the unit square) and are unit-tested without a GPU. `LiquidGlass`
// owns the GL objects and returns null from `create` if anything about WebGL2 is unavailable, so
// the page can fall back to the 2D effects.
import { FRAGMENT_SOURCE, SLOTS, VERTEX_SOURCE } from "./shader";
import type { Durations, RunningFx } from "./timeline";

export { SLOTS } from "./shader";

/** How long each effect stays on screen. The shader cannot draw anything older than 3.2 s. */
export const GL_DURATION_MS: Durations = { grab: 1900, drop: 2900 };
export const SHADER_MAX_AGE_S = 3.2;
/** The glass bead under the cursor forms quickly, holds, and is gone when the grab ends. */
export const HOLD_FADE_IN_S = 0.3;
const HOLD_FADE_OUT_FROM_S = 1.45;
/** About a million pixels is plenty for soft water and keeps big screens affordable. */
const MAX_PIXELS = 1_000_000;

const smooth = (e0: number, e1: number, v: number): number => {
  const t = Math.min(1, Math.max(0, (v - e0) / (e1 - e0)));
  return t * t * (3 - 2 * t);
};

/** Strength of the glass bead `ageSeconds` after a grab started: 0 → 1 → 0. */
export function holdAmount(ageSeconds: number): number {
  if (!Number.isFinite(ageSeconds) || ageSeconds <= 0) return 0;
  const end = GL_DURATION_MS.grab / 1000;
  return smooth(0, HOLD_FADE_IN_S, ageSeconds) * (1 - smooth(HOLD_FADE_OUT_FROM_S, end, ageSeconds));
}

export interface FrameUniforms {
  /** SLOTS × (x, y, age, kind); a negative age switches the slot off. */
  ripples: Float32Array;
  /** x, y, amount of the glass bead. */
  hold: [number, number, number];
}

/** What to tell the shader at `now` (ms). The newest SLOTS effects win; css pixels in. */
export function frameUniforms(running: readonly RunningFx[], now: number, w: number, h: number): FrameUniforms {
  const width = Number.isFinite(w) && w > 0 ? w : 1;
  const height = Number.isFinite(h) && h > 0 ? h : 1;
  const ripples = new Float32Array(SLOTS * 4);
  for (let i = 0; i < SLOTS; i++) ripples[i * 4 + 2] = -1;

  let hold: [number, number, number] = [0.5, 0.5, 0];
  running.slice(-SLOTS).forEach((e, i) => {
    const age = (now - e.start) / 1000;
    const nx = e.x / width;
    const ny = 1 - e.y / height;
    const live = Number.isFinite(age) && age >= 0 && age < SHADER_MAX_AGE_S;
    ripples.set([nx, ny, live ? age : -1, e.kind === "grab" ? 0 : 1], i * 4);
    if (live && e.kind === "grab") {
      const amount = holdAmount(age);
      if (amount > hold[2]) hold = [nx, ny, amount];
    }
  });
  return { ripples, hold };
}

/** Canvas pixels per css pixel: full size on small screens, about a million pixels on big ones. */
export function renderScale(cssWidth: number, cssHeight: number, dpr: number): number {
  const ratio = Number.isFinite(dpr) && dpr > 0 ? Math.min(1, dpr) : 1;
  const pixels = cssWidth * cssHeight;
  if (!Number.isFinite(pixels) || pixels <= 0) return ratio;
  return Math.min(ratio, Math.sqrt(MAX_PIXELS / pixels));
}

function compile(gl: WebGL2RenderingContext, type: number, source: string): WebGLShader | null {
  const shader = gl.createShader(type);
  if (!shader) return null;
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  if (gl.getShaderParameter(shader, gl.COMPILE_STATUS)) return shader;
  console.warn("fx: shader did not compile:", gl.getShaderInfoLog(shader));
  gl.deleteShader(shader);
  return null;
}

export class LiquidGlass {
  private lostContext = false;

  private constructor(
    private readonly gl: WebGL2RenderingContext,
    private readonly program: WebGLProgram,
    private readonly locations: { res: WebGLUniformLocation; ripples: WebGLUniformLocation; hold: WebGLUniformLocation },
    private readonly canvas: HTMLCanvasElement,
  ) {
    canvas.addEventListener("webglcontextlost", (e) => {
      e.preventDefault();
      this.lostContext = true;
    });
  }

  /** The WebGL2 renderer on `canvas`, or null when it cannot be had (the 2D effects take over). */
  static create(canvas: HTMLCanvasElement): LiquidGlass | null {
    let gl: WebGL2RenderingContext | null = null;
    try {
      gl = canvas.getContext("webgl2", {
        alpha: true,
        premultipliedAlpha: true,
        antialias: false,
        depth: false,
        stencil: false,
        preserveDrawingBuffer: false,
        powerPreference: "low-power",
      });
    } catch {
      return null;
    }
    if (!gl) return null;

    const vs = compile(gl, gl.VERTEX_SHADER, VERTEX_SOURCE);
    const fs = compile(gl, gl.FRAGMENT_SHADER, FRAGMENT_SOURCE);
    const program = vs && fs ? gl.createProgram() : null;
    if (!program || !vs || !fs) return null;
    gl.attachShader(program, vs);
    gl.attachShader(program, fs);
    gl.linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
      console.warn("fx: shader did not link:", gl.getProgramInfoLog(program));
      return null;
    }
    const res = gl.getUniformLocation(program, "uRes");
    const ripples = gl.getUniformLocation(program, "uR");
    const hold = gl.getUniformLocation(program, "uHold");
    const buffer = gl.createBuffer();
    if (!res || !ripples || !hold || !buffer) return null;

    gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
    const attribute = gl.getAttribLocation(program, "aPos");
    gl.enableVertexAttribArray(attribute);
    gl.vertexAttribPointer(attribute, 2, gl.FLOAT, false, 0, 0);
    gl.disable(gl.DEPTH_TEST);
    gl.disable(gl.BLEND);
    return new LiquidGlass(gl, program, { res, ripples, hold }, canvas);
  }

  get lost(): boolean {
    return this.lostContext || this.gl.isContextLost();
  }

  /** Size the drawing buffer for a window of cssWidth × cssHeight. */
  resize(cssWidth: number, cssHeight: number, dpr: number): void {
    const scale = renderScale(cssWidth, cssHeight, dpr);
    this.canvas.width = Math.max(1, Math.round(cssWidth * scale));
    this.canvas.height = Math.max(1, Math.round(cssHeight * scale));
  }

  draw(running: readonly RunningFx[], now: number, cssWidth: number, cssHeight: number): void {
    if (this.lost) return;
    const { gl } = this;
    const u = frameUniforms(running, now, cssWidth, cssHeight);
    gl.viewport(0, 0, this.canvas.width, this.canvas.height);
    gl.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.useProgram(this.program);
    gl.uniform2f(this.locations.res, this.canvas.width, this.canvas.height);
    gl.uniform4fv(this.locations.ripples, u.ripples);
    gl.uniform3f(this.locations.hold, u.hold[0], u.hold[1], u.hold[2]);
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
  }

  /** Leave the canvas fully transparent. */
  clear(): void {
    if (this.lost) return;
    this.gl.clearColor(0, 0, 0, 0);
    this.gl.clear(this.gl.COLOR_BUFFER_BIT);
  }
}

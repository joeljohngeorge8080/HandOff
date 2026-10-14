// Canvas drawing for the effect shapes (ADR-062): the hand, the photo and the dashed ghost.
// The hand and the photo are painted once into small off-screen canvases and then scaled and
// faded as images, so a translucent hand has no seams where its parts overlap.
import { ACCENT, PHOTO_H, PHOTO_W, type Shape } from "./effects";

type Ctx = CanvasRenderingContext2D;

const SKIN = "rgb(233, 185, 149)";
const SKIN_LINE = "rgb(110, 70, 48)";
const SKIN_DETAIL = "rgb(169, 113, 79)";
const SS = 2; // off-screen supersampling

function rrect(c: Ctx, x: number, y: number, w: number, h: number, r: number): void {
  const k = Math.max(0, Math.min(r, w / 2, h / 2));
  c.beginPath();
  c.moveTo(x + k, y);
  c.arcTo(x + w, y, x + w, y + h, k);
  c.arcTo(x + w, y + h, x, y + h, k);
  c.arcTo(x, y + h, x, y, k);
  c.arcTo(x, y, x + w, y, k);
  c.closePath();
}

function rot(p: readonly [number, number], c: readonly [number, number], deg: number): [number, number] {
  const a = (deg * Math.PI) / 180;
  const dx = p[0] - c[0];
  const dy = p[1] - c[1];
  return [c[0] + dx * Math.cos(a) - dy * Math.sin(a), c[1] + dx * Math.sin(a) + dy * Math.cos(a)];
}

type Part =
  | { cap: [number, number]; to: [number, number]; r: number }
  | { rr: readonly [number, number, number, number]; r: number };

const THUMB_FROM = rot([-49, 19], [-40, 48], -38);
const THUMB_TO = rot([-49, 47], [-40, 48], -38);
const OPEN: Part[] = [
  { cap: THUMB_FROM, to: THUMB_TO, r: 9 },
  { rr: [-34, -64, 15, 70], r: 7.5 },
  { rr: [-17, -76, 15, 82], r: 7.5 },
  { rr: [0, -68, 15, 74], r: 7.5 },
  { rr: [17, -52, 15, 58], r: 7.5 },
  { rr: [-34, -8, 68, 62], r: 26 },
];
const FIST: Part[] = [
  { rr: [-38, -16, 76, 62], r: 24 },
  { rr: [-36, -30, 17, 34], r: 8.5 },
  { rr: [-17.5, -32, 17, 36], r: 8.5 },
  { rr: [1, -31, 17, 35], r: 8.5 },
  { rr: [19, -27, 17, 31], r: 8.5 },
  { rr: [-34, 18, 48, 20], r: 10 },
];

function paint(c: Ctx, parts: Part[], color: string, grow: number): void {
  c.fillStyle = color;
  c.strokeStyle = color;
  for (const p of parts) {
    if ("cap" in p) {
      c.lineCap = "round";
      c.lineWidth = (p.r + grow) * 2;
      c.beginPath();
      c.moveTo(p.cap[0], p.cap[1]);
      c.lineTo(p.to[0], p.to[1]);
      c.stroke();
    } else {
      const [x, y, w, h] = p.rr;
      rrect(c, x - grow, y - grow, w + 2 * grow, h + 2 * grow, p.r + grow);
      c.fill();
    }
  }
}

const HAND_BOX = 260;

function makeHand(fist: boolean): HTMLCanvasElement {
  const cv = document.createElement("canvas");
  cv.width = cv.height = HAND_BOX * SS;
  const c = cv.getContext("2d") as Ctx;
  c.scale(SS, SS);
  c.translate(HAND_BOX / 2, HAND_BOX / 2);
  const parts = fist ? FIST : OPEN;
  paint(c, parts, SKIN_LINE, 3.5);
  paint(c, parts, SKIN, 0);
  if (fist) {
    c.strokeStyle = SKIN_DETAIL;
    c.lineWidth = 2;
    c.lineCap = "butt";
    for (const [x, y0, y1] of [[-17.5, -20, 6], [1, -20, 6], [19, -16, 6]] as const) {
      c.beginPath();
      c.moveTo(x, y0);
      c.lineTo(x, y1);
      c.stroke();
    }
    rrect(c, -34, 18, 48, 20, 10);
    c.stroke();
  }
  return cv;
}

function makePhoto(): HTMLCanvasElement {
  const cv = document.createElement("canvas");
  cv.width = PHOTO_W * SS;
  cv.height = PHOTO_H * SS;
  const c = cv.getContext("2d") as Ctx;
  c.scale(SS, SS);
  rrect(c, 0, 0, PHOTO_W, PHOTO_H, 12);
  c.save();
  c.clip();
  const sky = c.createLinearGradient(0, 0, 0, PHOTO_H);
  sky.addColorStop(0, "rgb(43, 27, 82)");
  sky.addColorStop(0.6, "rgb(184, 69, 107)");
  sky.addColorStop(1, "rgb(255, 154, 98)");
  c.fillStyle = sky;
  c.fillRect(0, 0, PHOTO_W, PHOTO_H);
  c.fillStyle = "rgb(255, 210, 122)";
  c.beginPath();
  c.arc(105, 47, 16, 0, Math.PI * 2);
  c.fill();
  const ridge = (color: string, pts: number[][]): void => {
    c.fillStyle = color;
    c.beginPath();
    pts.forEach(([x, y], i) => (i ? c.lineTo(x as number, y as number) : c.moveTo(x as number, y as number)));
    c.closePath();
    c.fill();
  };
  ridge("rgb(42, 20, 80)", [[0, 110], [0, 69], [35, 39], [65, 79], [95, 59], [146, 97], [146, 110]]);
  ridge("rgb(23, 10, 51)", [[0, 110], [0, 89], [27, 69], [59, 95], [89, 81], [121, 99], [146, 85], [146, 110]]);
  c.restore();
  c.strokeStyle = "rgba(255, 255, 255, 0.27)";
  c.lineWidth = 2;
  rrect(c, 1, 1, PHOTO_W - 2, PHOTO_H - 2, 11);
  c.stroke();
  return cv;
}

const cache: { open?: HTMLCanvasElement; fist?: HTMLCanvasElement; photo?: HTMLCanvasElement } = {};

function dashedRect(c: Ctx, x: number, y: number, w: number, h: number): void {
  c.setLineDash([5, 5]);
  c.lineWidth = 2;
  rrect(c, x, y, w, h, 12);
  c.stroke();
  c.setLineDash([]);
}

/** Draw one shape. All inputs come from effects.ts and are already finite and clamped. */
export function drawShape(c: Ctx, s: Shape): void {
  if (s.alpha <= 0.01) return;
  c.save();
  c.globalAlpha = Math.min(1, s.alpha);
  if (s.kind === "ring") {
    c.strokeStyle = `rgb(${s.color[0]}, ${s.color[1]}, ${s.color[2]})`;
    c.lineWidth = s.width;
    c.beginPath();
    c.arc(s.x, s.y, s.r, 0, Math.PI * 2);
    c.stroke();
  } else if (s.kind === "ghost") {
    const x = s.x - s.w / 2;
    const y = s.y - s.h / 2;
    c.fillStyle = `rgba(${ACCENT[0]}, ${ACCENT[1]}, ${ACCENT[2]}, 0.08)`;
    rrect(c, x, y, s.w, s.h, 12);
    c.fill();
    c.strokeStyle = `rgb(${ACCENT[0]}, ${ACCENT[1]}, ${ACCENT[2]})`;
    dashedRect(c, x, y, s.w, s.h);
  } else if (s.kind === "photo") {
    const x = s.x - s.w / 2;
    const y = s.y - s.h / 2;
    if (s.shadow > 0.02) {
      c.fillStyle = `rgba(0, 0, 0, ${0.06 * s.shadow})`;
      for (let i = 0; i < 7; i++) {
        rrect(c, x - i * 2, y + 10 - i * 2, s.w + i * 4, s.h + i * 4, 12 + i * 2);
        c.fill();
      }
    }
    cache.photo ??= makePhoto();
    c.drawImage(cache.photo, x, y, s.w, s.h);
  } else {
    const img = s.pose === "open" ? (cache.open ??= makeHand(false)) : (cache.fist ??= makeHand(true));
    const size = HAND_BOX * s.k;
    c.drawImage(img, s.x - size / 2, s.y - size / 2, size, size);
  }
  c.restore();
}

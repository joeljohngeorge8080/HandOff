#!/usr/bin/env python3
"""
Grab and Drop - an air-gesture file-transfer animation in Python (pygame).

A hand rises over a phone, closes into a fist to grab a photo, carries it
across to a tablet, then opens to drop it there.  Works the same on Windows
and Linux.

Setup:
    pip install pygame

Run:
    python grab_and_drop.py                    # play in a window (loops)
    python grab_and_drop.py --frames out_dir   # write one loop as PNG frames

Keys:  SPACE pause/play   R restart   S slow motion   ESC quit
"""
import argparse
import math
import os
import sys

import pygame

# ----------------------------------------------------------------------------
# Settings
# ----------------------------------------------------------------------------
W, H = 800, 540            # window size (stage is the top 800x460)
STAGE_H = 460
DURATION = 9.0             # seconds per loop
FPS = 60

BG = (12, 16, 24)
STAGE_BG = (15, 21, 32)
DEVICE = (26, 32, 43)
DEVICE_EDGE = (47, 56, 74)
SCREEN = (11, 14, 20)
LINE_A = (42, 51, 69)
LINE_B = (34, 43, 59)
TILE = (22, 29, 42)
ACCENT = (91, 157, 255)
TEXT = (232, 236, 244)
MUTED = (138, 148, 168)
SKIN = (233, 185, 149)
SKIN_LINE = (110, 70, 48)
SKIN_DETAIL = (169, 113, 79)


# ----------------------------------------------------------------------------
# Timeline helpers (keyframes with easing, like CSS @keyframes)
# ----------------------------------------------------------------------------
def _ease(name, u):
    if name == "out":
        return 1 - (1 - u) ** 3
    if name == "in":
        return u ** 2
    if name == "inout":
        return u * u * (3 - 2 * u)
    return u


def _mix(a, b, u):
    if isinstance(a, tuple):
        return tuple(x + (y - x) * u for x, y in zip(a, b))
    return a + (b - a) * u


def track(p, keys):
    """keys: list of (progress, value[, easing]); easing applies to the segment
    that starts at that key."""
    if p <= keys[0][0]:
        return keys[0][1]
    for i in range(len(keys) - 1):
        k0, k1 = keys[i], keys[i + 1]
        if k0[0] <= p <= k1[0]:
            span = k1[0] - k0[0]
            u = 1.0 if span == 0 else (p - k0[0]) / span
            e = k0[2] if len(k0) > 2 else "lin"
            return _mix(k0[1], k1[1], _ease(e, u))
    return keys[-1][1]


# Positions (stage coordinates)
PHONE_CARD = (170, 165)
HAND_PHONE = (170, 215)
HAND_TABLET = (605, 215)
CARRY_PHONE = (170, 287)
CARRY_TABLET = (605, 287)
DROP_SPOT = (605, 225)

HAND_POS = [
    (0.00, (120, 540), "out"), (0.20, (170, 215), "lin"), (0.29, (170, 215), "inout"),
    (0.60, (605, 215), "lin"), (0.69, (605, 215), "in"), (0.80, (690, 520), "lin"),
    (1.00, (120, 540)),
]
HAND_ALPHA = [(0, 0.0), (0.04, 0.95), (0.69, 0.95), (0.80, 0.0), (1, 0.0)]
OPEN_ALPHA = [(0, 1.0), (0.24, 1.0), (0.27, 0.0), (0.67, 0.0), (0.695, 1.0), (1, 1.0)]
FIST_ALPHA = [(0, 0.0), (0.24, 0.0), (0.27, 1.0), (0.66, 1.0), (0.69, 0.0), (1, 0.0)]
FIST_SCALE = [(0, 1.0), (0.24, 1.0), (0.27, 0.92), (0.31, 1.0), (1, 1.0)]

PAY_POS = [
    (0.00, PHONE_CARD, "lin"), (0.24, PHONE_CARD, "inout"), (0.29, CARRY_PHONE, "inout"),
    (0.60, CARRY_TABLET, "lin"), (0.69, CARRY_TABLET, "out"), (0.75, DROP_SPOT, "lin"),
    (0.97, DROP_SPOT, "lin"), (0.975, PHONE_CARD, "lin"), (1.00, PHONE_CARD),
]
PAY_SCALE = [
    (0, 1.0), (0.24, 1.0, "inout"), (0.29, 0.5), (0.69, 0.5, "out"), (0.75, 1.35),
    (0.97, 1.35), (0.975, 1.0), (1, 1.0),
]
PAY_ALPHA = [(0, 1.0), (0.94, 1.0), (0.97, 0.0), (0.975, 0.0), (1, 1.0)]
PAY_SHADOW = [(0, 0.0), (0.24, 0.0), (0.29, 1.0), (0.69, 1.0), (0.75, 0.0), (1, 0.0)]
GHOST_ALPHA = [(0, 0.0), (0.26, 0.0), (0.31, 1.0), (0.94, 1.0), (0.97, 0.0), (1, 0.0)]
ZONE_ALPHA = [(0, 0.0), (0.36, 0.0), (0.42, 1.0), (0.50, 0.5), (0.58, 1.0),
              (0.68, 1.0), (0.72, 0.0), (1, 0.0)]
GLOW_ALPHA = [(0, 0.0), (0.72, 0.0), (0.78, 1.0), (0.92, 0.55), (0.97, 0.0), (1, 0.0)]

CAPTIONS = [
    ("Hold your open hand up to the screen", 0.02, 0.20),
    ("Close it into a fist to grab", 0.23, 0.36),
    ("Carry it over to the other device", 0.39, 0.60),
    ("Open your hand to drop", 0.63, 0.76),
    ("The photo arrives", 0.79, 0.94),
]


# ----------------------------------------------------------------------------
# Drawing helpers
# ----------------------------------------------------------------------------
def rrect(surf, color, rect, radius, width=0):
    pygame.draw.rect(surf, color, rect, width, border_radius=int(radius))


def capsule(surf, color, p1, p2, r):
    pygame.draw.line(surf, color, p1, p2, int(r * 2))
    pygame.draw.circle(surf, color, (int(p1[0]), int(p1[1])), int(r))
    pygame.draw.circle(surf, color, (int(p2[0]), int(p2[1])), int(r))


def dashed_rect(surf, color, rect, dash=7, gap=6, width=2):
    x, y, w, h = rect
    for start in range(0, int(w), dash + gap):
        end = min(start + dash, w)
        pygame.draw.line(surf, color, (x + start, y), (x + end, y), width)
        pygame.draw.line(surf, color, (x + start, y + h), (x + end, y + h), width)
    for start in range(0, int(h), dash + gap):
        end = min(start + dash, h)
        pygame.draw.line(surf, color, (x, y + start), (x, y + end), width)
        pygame.draw.line(surf, color, (x + w, y + start), (x + w, y + end), width)


def make_photo():
    """The photo that gets carried: sunset sky, sun and two mountain ridges."""
    w, h = 146, 110
    photo = pygame.Surface((w, h), pygame.SRCALPHA)
    stops = [(0.0, (43, 27, 82)), (0.6, (184, 69, 107)), (1.0, (255, 154, 98))]
    for y in range(h):
        t = y / (h - 1)
        for (t0, c0), (t1, c1) in zip(stops, stops[1:]):
            if t0 <= t <= t1:
                u = (t - t0) / (t1 - t0)
                col = tuple(int(c0[i] + (c1[i] - c0[i]) * u) for i in range(3))
                break
        pygame.draw.line(photo, col, (0, y), (w, y))
    pygame.draw.circle(photo, (255, 210, 122), (105, 47), 16)
    pygame.draw.polygon(photo, (42, 20, 80), [(0, 110), (0, 69), (35, 39), (65, 79),
                                              (95, 59), (146, 97), (146, 110)])
    pygame.draw.polygon(photo, (23, 10, 51), [(0, 110), (0, 89), (27, 69), (59, 95),
                                              (89, 81), (121, 99), (146, 85), (146, 110)])
    mask = pygame.Surface((w, h), pygame.SRCALPHA)
    rrect(mask, (255, 255, 255, 255), (0, 0, w, h), 12)
    photo.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
    rrect(photo, (255, 255, 255, 70), (0, 0, w, h), 12, 2)
    return photo


def make_stage():
    """Everything that never moves: the phone and the tablet."""
    s = pygame.Surface((W, STAGE_H))
    s.fill(STAGE_BG)
    # phone
    rrect(s, DEVICE, (70, 60, 200, 360), 34)
    rrect(s, DEVICE_EDGE, (70, 60, 200, 360), 34, 2)
    rrect(s, SCREEN, (80, 70, 180, 340), 26)
    rrect(s, (0, 0, 0), (145, 77, 50, 12), 6)
    rrect(s, LINE_A, (94, 96, 70, 6), 3)
    rrect(s, LINE_A, (94, 236, 120, 6), 3)
    rrect(s, LINE_B, (94, 252, 90, 6), 3)
    rrect(s, LINE_B, (94, 268, 105, 6), 3)
    rrect(s, TILE, (94, 292, 74, 70), 10)
    rrect(s, TILE, (172, 292, 74, 70), 10)
    rrect(s, (58, 68, 88), (140, 397, 60, 4), 2)
    # tablet
    rrect(s, DEVICE, (466, 106, 278, 238), 22)
    rrect(s, DEVICE_EDGE, (466, 106, 278, 238), 22, 2)
    rrect(s, SCREEN, (478, 118, 254, 214), 12)
    pygame.draw.circle(s, SCREEN, (472, 225), 2)
    rrect(s, LINE_A, (492, 128, 60, 7), 3)
    pygame.draw.circle(s, LINE_B, (712, 131), 4)
    pygame.draw.circle(s, LINE_B, (698, 131), 4)
    for x in (492, 552, 612):
        rrect(s, TILE, (x, 308, 52, 16), 6)
    return s


# Hand shapes in local coordinates, centred on the palm.
def _rot(pt, centre, deg):
    a = math.radians(deg)
    dx, dy = pt[0] - centre[0], pt[1] - centre[1]
    return (centre[0] + dx * math.cos(a) - dy * math.sin(a),
            centre[1] + dx * math.sin(a) + dy * math.cos(a))


OPEN_SHAPES = [
    ("cap", _rot((-49, 19), (-40, 48), -38), _rot((-49, 47), (-40, 48), -38), 9),
    ("rr", (-34, -64, 15, 70), 7.5),
    ("rr", (-17, -76, 15, 82), 7.5),
    ("rr", (0, -68, 15, 74), 7.5),
    ("rr", (17, -52, 15, 58), 7.5),
    ("rr", (-34, -8, 68, 62), 26),
]
FIST_SHAPES = [
    ("rr", (-38, -16, 76, 62), 24),
    ("rr", (-36, -30, 17, 34), 8.5),
    ("rr", (-17.5, -32, 17, 36), 8.5),
    ("rr", (1, -31, 17, 35), 8.5),
    ("rr", (19, -27, 17, 31), 8.5),
    ("rr", (-34, 18, 48, 20), 10),
]


def draw_hand(target, shapes, fist, centre, k, alpha):
    """Draw one hand pose (outline pass, then skin pass) with a given alpha."""
    if alpha <= 0.01:
        return
    size = int(260 * max(k, 1))
    layer = pygame.Surface((size, size), pygame.SRCALPHA)
    cx = cy = size / 2

    def pt(p):
        return (cx + p[0] * k, cy + p[1] * k)

    def paint(color, grow):
        for sh in shapes:
            if sh[0] == "cap":
                capsule(layer, color, pt(sh[1]), pt(sh[2]), sh[3] * k + grow)
            else:
                x, y, w, h = sh[1]
                rrect(layer, color,
                      (cx + x * k - grow, cy + y * k - grow, w * k + 2 * grow, h * k + 2 * grow),
                      sh[2] * k + grow)

    paint(SKIN_LINE, 3.5 * k)      # outline
    paint(SKIN, 0)                 # skin
    if fist:                       # curled-finger details
        for x, y0, y1 in ((-17.5, -20, 6), (1, -20, 6), (19, -16, 6)):
            pygame.draw.line(layer, SKIN_DETAIL, pt((x, y0)), pt((x, y1)), max(1, int(2 * k)))
        rrect(layer, SKIN_DETAIL, (cx - 34 * k, cy + 18 * k, 48 * k, 20 * k), 10 * k, max(1, int(2 * k)))
    layer.set_alpha(int(255 * min(1.0, alpha)))
    target.blit(layer, (centre[0] - cx, centre[1] - cy))


# ----------------------------------------------------------------------------
# Animation
# ----------------------------------------------------------------------------
class GrabAndDrop:
    def __init__(self):
        self.stage = make_stage()
        self.photo = make_photo()
        self.fx = pygame.Surface((W, STAGE_H), pygame.SRCALPHA)
        self.layer = pygame.Surface((W, STAGE_H), pygame.SRCALPHA)

    def draw(self, screen, p, font, small):
        screen.fill(BG)
        stage = self.stage.copy()
        fx = self.fx
        fx.fill((0, 0, 0, 0))

        # ghost outline where the photo used to be
        ga = track(p, GHOST_ALPHA)
        if ga > 0.01:
            rrect(fx, (*ACCENT, int(20 * ga)), (97, 110, 146, 110), 12)
            dashed_rect(fx, (*ACCENT, int(255 * ga)), (97, 110, 146, 110), 5, 5, 2)
        # drop zone on the tablet
        za = track(p, ZONE_ALPHA)
        if za > 0.01:
            rrect(fx, (*ACCENT, int(25 * za)), (507, 151, 197, 148), 14)
            dashed_rect(fx, (*ACCENT, int(255 * za)), (507, 151, 197, 148), 7, 6, 2)
        # tablet glow after the drop
        gl = track(p, GLOW_ALPHA)
        if gl > 0.01:
            rrect(fx, (*ACCENT, int(255 * gl)), (478, 118, 254, 214), 12, 3)
        # ripples
        if 0.24 <= p <= 0.37:
            u = (p - 0.24) / 0.13
            pygame.draw.circle(fx, (*ACCENT, int(217 * (1 - u))), PHONE_CARD, int(78 * (0.4 + 1.1 * u)), 3)
        if 0.68 <= p <= 0.82:
            u = (p - 0.68) / 0.14
            pygame.draw.circle(fx, (*ACCENT, int(217 * (1 - u))), DROP_SPOT, int(96 * (0.4 + 1.2 * u)), 3)

        # photo: soft shadow while lifted, then the photo itself
        pos = track(p, PAY_POS)
        sc = track(p, PAY_SCALE)
        pa = track(p, PAY_ALPHA)
        sh = track(p, PAY_SHADOW)
        pw, ph = int(146 * sc), int(110 * sc)
        if sh * pa > 0.02:
            for i in range(7):
                rrect(fx, (0, 0, 0, int(16 * sh * pa)),
                      (pos[0] - pw / 2 - i * 2, pos[1] - ph / 2 + 10 - i * 2, pw + i * 4, ph + i * 4),
                      12 * sc + i * 2)
        stage.blit(fx, (0, 0))
        if pa > 0.01 and pw > 2 and ph > 2:
            img = pygame.transform.smoothscale(self.photo, (pw, ph))
            img.set_alpha(int(255 * pa))
            stage.blit(img, (pos[0] - pw / 2, pos[1] - ph / 2))

        # hand on top
        hp = track(p, HAND_POS)
        ha = track(p, HAND_ALPHA)
        draw_hand(stage, OPEN_SHAPES, False, hp, 1.15, ha * track(p, OPEN_ALPHA))
        draw_hand(stage, FIST_SHAPES, True, hp, 1.15 * track(p, FIST_SCALE), ha * track(p, FIST_ALPHA))

        screen.blit(stage, (0, 0))

        # caption, progress bar and key hints
        for text, a, b in CAPTIONS:
            fade = 0.02
            if a - fade <= p <= b + fade:
                if p < a:
                    k = (p - (a - fade)) / fade
                elif p > b:
                    k = 1 - (p - b) / fade
                else:
                    k = 1
                img = font.render(text, True, TEXT)
                img.set_alpha(int(255 * max(0, min(1, k))))
                screen.blit(img, ((W - img.get_width()) // 2, STAGE_H + 20))
        pygame.draw.rect(screen, LINE_A, (40, STAGE_H + 62, W - 80, 3), border_radius=2)
        pygame.draw.rect(screen, ACCENT, (40, STAGE_H + 62, int((W - 80) * p), 3), border_radius=2)
        hint = small.render("SPACE pause   R restart   S slow motion   ESC quit", True, MUTED)
        screen.blit(hint, ((W - hint.get_width()) // 2, STAGE_H + 82))


def pick_font(size, bold=False):
    return pygame.font.SysFont("segoeui,helveticaneue,dejavusans,arial", size, bold=bold)


def run_window():
    pygame.init()
    pygame.display.set_caption("Grab and Drop")
    screen = pygame.display.set_mode((W, H), pygame.SCALED | pygame.RESIZABLE)
    clock = pygame.time.Clock()
    anim = GrabAndDrop()
    font, small = pick_font(22, True), pick_font(14)
    t, paused, slow = 0.0, False, False
    while True:
        for e in pygame.event.get():
            if e.type == pygame.QUIT or (e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE):
                pygame.quit()
                return
            if e.type == pygame.KEYDOWN:
                if e.key == pygame.K_SPACE:
                    paused = not paused
                elif e.key == pygame.K_r:
                    t, paused = 0.0, False
                elif e.key == pygame.K_s:
                    slow = not slow
        dt = clock.tick(FPS) / 1000.0
        if not paused:
            t += dt * (0.5 if slow else 1.0)
        anim.draw(screen, (t % DURATION) / DURATION, font, small)
        pygame.display.flip()


def run_frames(out_dir, fps=30):
    """Save one full loop as numbered PNGs (handy for making a GIF or video)."""
    os.makedirs(out_dir, exist_ok=True)
    pygame.init()
    screen = pygame.display.set_mode((W, H))
    anim = GrabAndDrop()
    font, small = pick_font(22, True), pick_font(14)
    total = int(DURATION * fps)
    for i in range(total):
        anim.draw(screen, i / total, font, small)
        pygame.image.save(screen, os.path.join(out_dir, f"frame_{i:04d}.png"))
    pygame.quit()
    print(f"Wrote {total} frames to {out_dir}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Grab and Drop air-gesture animation")
    ap.add_argument("--frames", metavar="DIR", help="export one loop as PNG frames instead of opening a window")
    args = ap.parse_args()
    if args.frames:
        run_frames(args.frames)
    else:
        run_window()

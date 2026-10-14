"""Landmarks -> HandSample. Pure; the camera and MediaPipe stay in tracker.py."""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from handoff import config
from handoff.cv.filters import OneEuro
from handoff.cv.gestures import HandSample
from handoff.cv.relative import RelativePointer

WRIST, THUMB_TIP, INDEX_MCP, INDEX_TIP, MIDDLE_MCP, MIDDLE_TIP = 0, 4, 5, 8, 9, 12
# (mcp, pip, dip, tip) of the index, middle, ring and pinky fingers.
INDEX = (5, 6, 7, 8)
MIDDLE = (9, 10, 11, 12)
RING = (13, 14, 15, 16)
PINKY = (17, 18, 19, 20)

POSE_POINT = "point"  # only the index finger out (or pinched to the thumb): moves the cursor
POSE_OPEN = "open"  # open palm
POSE_FIST = "fist"  # closed palm
POSE_SCROLL = "scroll"  # index and middle straight and together, the rest curled: scrolls
POSE_OTHER = "other"
# The pointer rides on a point that does not move when the fingers close (the thumb and index
# tips do), so pinching does not shove the cursor off its target.
ANCHOR_WEIGHT = 0.75  # index knuckle; the rest is the wrist


class Landmark(Protocol):
    x: float
    y: float
    z: float


@dataclass(frozen=True)
class Detection:
    """One frame's hand: palm anchor and index fingertip in camera-normalised coordinates, the
    pinch ratio, and the hand size that converts camera units into hand sizes (ADR-065)."""

    nx: float
    ny: float
    pinch: float
    pose: str = POSE_POINT
    tips_y: float | None = None  # mean height of the index and middle fingertips (scrolling)
    tip_x: float | None = None  # index fingertip; None = use the palm anchor
    tip_y: float | None = None
    per_x: float | None = None  # hand sizes per camera-normalised unit, x and y
    per_y: float | None = None


def analyze(
    lm: Sequence[Landmark],
    frame_size: tuple[int, int],
    world: Sequence[Landmark] | None = None,
    use_world: bool = False,
) -> Detection | None:
    """None for a malformed or degenerate hand (fewer than 21 points, zero hand size)."""
    if len(lm) < 21:
        return None
    w, h = frame_size

    def d2(a: int, b: int) -> float:
        return math.hypot((lm[a].x - lm[b].x) * w, (lm[a].y - lm[b].y) * h)

    def d3(pts: Sequence[Landmark], a: int, b: int) -> float:
        return math.dist((pts[a].x, pts[a].y, pts[a].z), (pts[b].x, pts[b].y, pts[b].z))

    if use_world and world is not None and len(world) >= 21:
        size, gap = d3(world, WRIST, MIDDLE_MCP), d3(world, THUMB_TIP, INDEX_TIP)
    else:
        size, gap = d2(WRIST, MIDDLE_MCP), d2(THUMB_TIP, INDEX_TIP)
    if not math.isfinite(size) or size < 1e-6:
        return None
    pose = _pose(lm, w, h, gap / size, d2(INDEX_TIP, MIDDLE_TIP) / size)
    nx = ANCHOR_WEIGHT * lm[INDEX_MCP].x + (1 - ANCHOR_WEIGHT) * lm[WRIST].x
    ny = ANCHOR_WEIGHT * lm[INDEX_MCP].y + (1 - ANCHOR_WEIGHT) * lm[WRIST].y
    if not all(math.isfinite(v) for v in (nx, ny, gap)):
        return None
    tips_y = (lm[INDEX_TIP].y + lm[MIDDLE_TIP].y) / 2
    hand_px = d2(WRIST, MIDDLE_MCP)  # in the image, whatever measured the pinch
    if not math.isfinite(hand_px) or hand_px < 1e-6:
        return None
    return Detection(
        nx=nx, ny=ny, pinch=gap / size, pose=pose, tips_y=tips_y,
        tip_x=lm[INDEX_TIP].x, tip_y=lm[INDEX_TIP].y, per_x=w / hand_px, per_y=h / hand_px,
    )  # fmt: skip


def _bend(lm: Sequence[Landmark], w: int, h: int, finger: tuple[int, int, int, int]) -> float:
    """Sum of the finger's three joint angles in degrees: 0 straight, ~180+ fully curled.

    3D (MediaPipe's z is on the x scale), so a fist seen knuckles-first still reads as curled;
    the old 2D tip-to-wrist ratio read it as half open."""
    pts = [(lm[i].x * w, lm[i].y * h, lm[i].z * w) for i in (WRIST, *finger)]
    total = 0.0
    for a, b, c in zip(pts, pts[1:], pts[2:], strict=False):
        u = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
        v = (c[0] - b[0], c[1] - b[1], c[2] - b[2])
        n = math.hypot(*u) * math.hypot(*v)
        if n < 1e-9:
            continue  # coincident joints: no angle to measure
        cos = (u[0] * v[0] + u[1] * v[1] + u[2] * v[2]) / n
        total += math.degrees(math.acos(min(max(cos, -1.0), 1.0)))
    return total


def _pose(lm: Sequence[Landmark], w: int, h: int, pinch: float, tips: float) -> str:
    """Pointing needs the middle, ring and pinky curled. The index is extended, or curled into
    a pinch with the thumb (a click); a fist with the thumb over it is not that pinch."""
    index, middle, ring, pinky = (_bend(lm, w, h, f) for f in (INDEX, MIDDLE, RING, PINKY))
    if not all(math.isfinite(b) for b in (index, middle, ring, pinky)):
        return POSE_OTHER

    def ext(b: float) -> bool:
        return b < config.CV_BEND_EXTENDED

    def curled(b: float) -> bool:
        return b > config.CV_BEND_CURLED

    if ext(index) and ext(middle) and ext(ring) and ext(pinky):
        return POSE_OPEN
    if curled(ring) and curled(pinky):
        if ext(index) and ext(middle):
            return POSE_SCROLL if tips < config.CV_SCROLL_TIPS_RATIO else POSE_OTHER
        if curled(middle):
            if not curled(index):
                return POSE_POINT
            rest = (middle + ring + pinky) / 3
            pinching = pinch < config.CV_PINCH_OFF and index <= rest - config.CV_PINCH_INDEX_MARGIN
            return POSE_POINT if pinching else POSE_FIST
    return POSE_OTHER


class SampleBuilder:
    """Smooths detections and turns hand movement into a cursor target (touchpad-style)."""

    def __init__(
        self,
        screen: tuple[int, int],
        position: Callable[[], tuple[float, float]],
        scroll: bool = False,
    ) -> None:
        self.screen = screen
        self._scroll_enabled = scroll  # ADR-064: off unless the user turned two-finger scroll on
        self._fx = OneEuro(config.CV_MIN_CUTOFF, config.CV_BETA, config.CV_D_CUTOFF)
        self._fy = OneEuro(config.CV_MIN_CUTOFF, config.CV_BETA, config.CV_D_CUTOFF)
        # Units are hand sizes on both axes (ADR-065), so the pointer needs no camera aspect.
        self._pointer = RelativePointer(screen, (1, 1), position)
        self._steer: tuple[float, float, float, float, float, float] | None = None
        self._vx = self._vy = 0.0  # integrated steering position, in hand sizes
        self._last_seen = -1e9
        self._active = False
        self._point_since: float | None = None
        self._lost_since: float | None = None
        self._palm_at = -math.inf  # last frame showing an open palm or a fist
        self._pos = (screen[0] / 2, screen[1] / 2)
        self._sy = OneEuro(config.CV_MIN_CUTOFF, config.CV_BETA, config.CV_D_CUTOFF)
        self._scrolling = False
        self._scroll_since: float | None = None
        self._scroll_lost: float | None = None

    def _gate(self, t: float, det: Detection) -> bool:
        """Debounced "is the hand pointing". A pinch in progress always counts: a drag must not
        freeze because the pose flickered."""
        if det.pose in (POSE_OPEN, POSE_FIST):
            self._palm_at = t
        if det.pose == POSE_OPEN and det.pinch >= config.CV_PINCH_ON:
            # An open palm ends pointing at once, so the closing hand that follows cannot click
            # on its pinch-like frames. Not a fist: a real pinch click passes through fist-like
            # frames (measured). A held pinch (a drag) keeps pointing.
            self._active, self._point_since = False, None
        pointing = det.pose == POSE_POINT or (self._active and det.pinch < config.CV_PINCH_ON)
        if pointing:
            self._lost_since = None
            if self._point_since is None:
                self._point_since = t
            held = t - self._point_since >= config.CV_POINT_ON_SECONDS
            # Just after a palm or fist the "point" is likely a closing hand, not a click.
            settled = t - self._palm_at >= config.CV_POINT_AFTER_PALM_SECONDS
            if not self._active and held and settled:
                self._active = True
        else:
            self._point_since = None
            if self._lost_since is None:
                self._lost_since = t
            if self._active and t - self._lost_since >= config.CV_POINT_OFF_SECONDS:
                self._active = False
        return self._active

    def _scroll_gate(self, t: float, det: Detection) -> bool:
        """Debounced "is the hand in the scroll pose", like the pointing gate. Once scrolling,
        curling the two fingers keeps it (it scrolls, measured: people flick the fingers)."""
        if not self._scroll_enabled:
            return False
        if det.pose == POSE_SCROLL or (self._scrolling and det.pose == POSE_FIST):
            self._scroll_lost = None
            if self._scroll_since is None:
                self._scroll_since = t
            if not self._scrolling and t - self._scroll_since >= config.CV_SCROLL_ON_SECONDS:
                self._scrolling = True
        else:
            self._scroll_since = None
            if self._scroll_lost is None:
                self._scroll_lost = t
            if self._scrolling and t - self._scroll_lost >= config.CV_SCROLL_OFF_SECONDS:
                self._scrolling = False
        return self._scrolling

    def _steering(self, t: float, det: Detection, reset: bool) -> tuple[float, float]:
        """Integrated pointer movement in hand sizes: the fingertip's movement while the pinch is
        open and steady, the palm's while it is closing, closed or changing fast (ADR-065)."""
        tip = (
            (det.tip_x, det.tip_y)
            if det.tip_x is not None and det.tip_y is not None
            else (det.nx, det.ny)
        )
        per_x = det.per_x if det.per_x is not None else 1 / config.CV_HAND_WIDTHS
        per_y = det.per_y if det.per_y is not None else 1 / config.CV_HAND_WIDTHS
        prev, self._steer = self._steer, (t, tip[0], tip[1], det.nx, det.ny, det.pinch)
        if reset or prev is None or t <= prev[0]:
            return self._vx, self._vy  # a fresh reference: the pointer re-references too
        pt, ptx, pty, ppx, ppy, ppinch = prev
        w = _tip_weight(det.pinch, (det.pinch - ppinch) / (t - pt))
        dx = w * (tip[0] - ptx) + (1 - w) * (det.nx - ppx)
        dy = w * (tip[1] - pty) + (1 - w) * (det.ny - ppy)
        self._vx += dx * per_x
        self._vy += dy * per_y
        return self._vx, self._vy

    def build(self, t: float, det: Detection) -> HandSample:
        lifted = (t - self._last_seen) > config.CV_REFERENCE_GAP_SECONDS
        self._last_seen = t
        was_scrolling = self._scrolling
        if self._scroll_gate(t, det) and not self._active:
            # Scrolling: the cursor stays put; the fingertips' height drives the wheel, so moving
            # the hand and curling the two fingers both scroll. Starting, or
            # coming back after a lift, re-references so the jump is not scrolled.
            if lifted or not was_scrolling:
                self._sy.reset()
            self._pointer.lift()
            self._point_since = None
            height = det.tips_y if det.tips_y is not None else det.ny
            return HandSample(
                t=t, x=self._pos[0], y=self._pos[1], pinch=det.pinch, pose=POSE_SCROLL,
                active=False, scroll_y=self._sy(height, t), reacquired=lifted or not was_scrolling,
            )  # fmt: skip  # pose SCROLL: the curled fingers of a flick are not a grab
        was_active = self._active
        active = self._gate(t, det)
        if lifted:
            self._fx.reset()  # the hand was lifted: do not smooth across the gap
            self._fy.reset()
        if not active:
            # Not pointing: the cursor stays put, and the next pointing frame re-references so
            # the distance the hand travelled meanwhile does not move it.
            self._pointer.lift()
            return HandSample(
                t=t, x=self._pos[0], y=self._pos[1], pinch=det.pinch, pose=det.pose, active=False
            )
        vx, vy = self._steering(t, det, reset=lifted or not was_active)
        x, y, reacquired = self._pointer.update(t, self._fx(vx, t), self._fy(vy, t))
        self._pos = (x, y)
        return HandSample(
            t=t, x=x, y=y, pinch=det.pinch, reacquired=reacquired or not was_active,
            pose=det.pose, active=True,
        )  # fmt: skip


def _tip_weight(pinch: float, pinch_speed: float) -> float:
    """How much the fingertip (vs the palm) steers: 1 with the pinch open, falling to 0 as it
    closes, and 0 while the pinch changes fast (a click in progress)."""
    if not (math.isfinite(pinch) and math.isfinite(pinch_speed)):
        return 0.0
    if abs(pinch_speed) > config.CV_PINCH_SPEED_GATE:
        return 0.0
    span = config.CV_TIP_FULL_PINCH - config.CV_PINCH_ON
    f = min(max((pinch - config.CV_PINCH_ON) / span, 0.0), 1.0)
    return f * f  # stays low until the fingers are well apart

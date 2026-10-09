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

WRIST, THUMB_TIP, INDEX_MCP, INDEX_TIP, MIDDLE_MCP = 0, 4, 5, 8, 9
# (pip, tip) of the index, middle, ring and pinky fingers.
INDEX_FINGER = (6, 8)
OTHER_FINGERS = ((10, 12), (14, 16), (18, 20))

POSE_POINT = "point"  # only the index finger out (or pinched to the thumb): moves the cursor
POSE_OPEN = "open"  # open palm
POSE_FIST = "fist"  # closed palm
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
    """One frame's hand: anchor in camera-normalised coordinates and the pinch ratio."""

    nx: float
    ny: float
    pinch: float
    pose: str = POSE_POINT


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
    pose = _pose(lm, w, h, gap / size)
    nx = ANCHOR_WEIGHT * lm[INDEX_MCP].x + (1 - ANCHOR_WEIGHT) * lm[WRIST].x
    ny = ANCHOR_WEIGHT * lm[INDEX_MCP].y + (1 - ANCHOR_WEIGHT) * lm[WRIST].y
    if not all(math.isfinite(v) for v in (nx, ny, gap)):
        return None
    return Detection(nx=nx, ny=ny, pinch=gap / size, pose=pose)


def _reach(lm: Sequence[Landmark], w: int, h: int, joint: int, tip: int) -> float:
    """How far the fingertip is from the wrist, relative to its middle joint (>1 = straight)."""
    base = math.hypot((lm[joint].x - lm[WRIST].x) * w, (lm[joint].y - lm[WRIST].y) * h)
    if base < 1e-6:
        return 0.0
    return math.hypot((lm[tip].x - lm[WRIST].x) * w, (lm[tip].y - lm[WRIST].y) * h) / base


def _pose(lm: Sequence[Landmark], w: int, h: int, pinch: float) -> str:
    """Pointing needs the middle, ring and pinky curled. The index is extended, or curled into
    a pinch with the thumb (a click); a fist with the thumb over it is not that pinch."""
    ext = config.CV_FINGER_EXTENDED_RATIO
    index = _reach(lm, w, h, *INDEX_FINGER)
    others = sum(_reach(lm, w, h, *f) > ext for f in OTHER_FINGERS)
    index_out = index > ext
    if others == 0:
        pinching = pinch < config.CV_PINCH_OFF and index >= config.CV_PINCH_INDEX_RATIO
        return POSE_POINT if index_out or pinching else POSE_FIST
    if index_out and others >= 2:
        return POSE_OPEN
    return POSE_OTHER


class SampleBuilder:
    """Smooths detections and turns hand movement into a cursor target (touchpad-style)."""

    def __init__(
        self, screen: tuple[int, int], position: Callable[[], tuple[float, float]]
    ) -> None:
        self.screen = screen
        self._fx = OneEuro(config.CV_MIN_CUTOFF, config.CV_BETA, config.CV_D_CUTOFF)
        self._fy = OneEuro(config.CV_MIN_CUTOFF, config.CV_BETA, config.CV_D_CUTOFF)
        self._pointer = RelativePointer(screen, config.CV_CAMERA_SIZE, position)
        self._last_seen = -1e9
        self._active = False
        self._point_since: float | None = None
        self._lost_since: float | None = None
        self._pos = (screen[0] / 2, screen[1] / 2)

    def _gate(self, t: float, det: Detection) -> bool:
        """Debounced "is the hand pointing". A pinch in progress always counts: a drag must not
        freeze because the pose flickered."""
        pointing = det.pose == POSE_POINT or (self._active and det.pinch < config.CV_PINCH_ON)
        if pointing:
            self._lost_since = None
            if self._point_since is None:
                self._point_since = t
            if not self._active and t - self._point_since >= config.CV_POINT_ON_SECONDS:
                self._active = True
        else:
            self._point_since = None
            if self._lost_since is None:
                self._lost_since = t
            if self._active and t - self._lost_since >= config.CV_POINT_OFF_SECONDS:
                self._active = False
        return self._active

    def build(self, t: float, det: Detection) -> HandSample:
        lifted = (t - self._last_seen) > config.CV_REFERENCE_GAP_SECONDS
        self._last_seen = t
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
        x, y, reacquired = self._pointer.update(t, self._fx(det.nx, t), self._fy(det.ny, t))
        self._pos = (x, y)
        return HandSample(
            t=t, x=x, y=y, pinch=det.pinch, reacquired=reacquired or not was_active,
            pose=det.pose, active=True,
        )  # fmt: skip

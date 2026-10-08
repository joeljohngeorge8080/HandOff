"""Landmarks -> HandSample. Pure; the camera and MediaPipe stay in tracker.py."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from handoff import config
from handoff.cv.filters import OneEuro
from handoff.cv.gestures import HandSample
from handoff.cv.mapping import to_screen

WRIST, THUMB_TIP, INDEX_MCP, INDEX_TIP, MIDDLE_MCP = 0, 4, 5, 8, 9
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
    nx = ANCHOR_WEIGHT * lm[INDEX_MCP].x + (1 - ANCHOR_WEIGHT) * lm[WRIST].x
    ny = ANCHOR_WEIGHT * lm[INDEX_MCP].y + (1 - ANCHOR_WEIGHT) * lm[WRIST].y
    if not all(math.isfinite(v) for v in (nx, ny, gap)):
        return None
    return Detection(nx=nx, ny=ny, pinch=gap / size)


class SampleBuilder:
    """Smooths detections into screen-space samples and notices when a hand comes back."""

    def __init__(self, screen: tuple[int, int]) -> None:
        self.screen = screen
        self._fx = OneEuro(config.CV_MIN_CUTOFF, config.CV_BETA, config.CV_D_CUTOFF)
        self._fy = OneEuro(config.CV_MIN_CUTOFF, config.CV_BETA, config.CV_D_CUTOFF)
        self._last_seen = -1e9

    def build(self, t: float, det: Detection) -> HandSample:
        reacquired = (t - self._last_seen) > config.CV_LOST_GRACE_SECONDS
        if reacquired:
            self._fx.reset()
            self._fy.reset()
        self._last_seen = t
        sx, sy = to_screen(det.nx, det.ny, self.screen, config.CV_X_RANGE, config.CV_Y_RANGE)
        return HandSample(
            t=t, x=self._fx(sx, t), y=self._fy(sy, t), pinch=det.pinch, reacquired=reacquired
        )

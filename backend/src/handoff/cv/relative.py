"""Touchpad-style pointer: the cursor moves by the change in hand position, with acceleration.

Pure: no camera, no OS (the caller passes time and a way to read the real cursor position).

Why relative: with an absolute mapping the hand has to cover the whole camera frame to cover the
screen. Here a small, brisk movement crosses the screen while a slow one stays pixel-accurate,
exactly like a touchpad. Taking the hand out of view is "lifting the finger": when it comes back
the cursor continues from where it was, it does not jump to the hand.
"""

from __future__ import annotations

import math
from collections.abc import Callable

from handoff import config


class RelativePointer:
    def __init__(
        self,
        screen: tuple[int, int],
        cam_size: tuple[int, int],
        position: Callable[[], tuple[float, float]],
    ) -> None:
        self._w, self._h = screen
        self._aspect = cam_size[1] / cam_size[0]  # camera height in camera-widths
        self._position = position
        self._cx = self._w / 2
        self._cy = self._h / 2
        self._ref: tuple[float, float] | None = None
        self._t: float | None = None
        self._speed = 0.0

    def update(self, t: float, nx: float, ny: float) -> tuple[float, float, bool]:
        """(cursor x, cursor y, re-referenced) for the hand anchor seen at time `t`.

        `re-referenced` is True on the first frame and after the hand was out of view; the
        cursor did not move because of the hand's jump, and was resynced to the real cursor.
        """
        if not (math.isfinite(nx) and math.isfinite(ny) and math.isfinite(t)):
            return self._cx, self._cy, False
        if self._t is None or self._ref is None or t - self._t > config.CV_REFERENCE_GAP_SECONDS:
            return self._rereference(t, nx, ny)
        dt = t - self._t
        if dt <= 0:
            return self._cx, self._cy, False
        dx = nx - self._ref[0]
        dy = (ny - self._ref[1]) * self._aspect
        self._ref, self._t = (nx, ny), t
        step = math.hypot(dx, dy)
        if step > config.CV_MAX_STEP:
            return self._cx, self._cy, False  # a tracking glitch, not a hand movement
        a = config.CV_SPEED_SMOOTHING
        self._speed += a * (step / dt - self._speed)
        gain = self._gain(self._speed)
        self._cx = min(max(self._cx + dx * gain * self._w, 0.0), self._w - 1.0)
        self._cy = min(max(self._cy + dy * gain * self._w, 0.0), self._h - 1.0)
        return self._cx, self._cy, False

    def lift(self) -> None:
        """The hand stopped pointing: the next update re-references like a returning hand."""
        self._t = None

    def _rereference(self, t: float, nx: float, ny: float) -> tuple[float, float, bool]:
        px, py = self._position()  # the user may have used the real mouse meanwhile
        if math.isfinite(px) and math.isfinite(py):
            self._cx = min(max(float(px), 0.0), self._w - 1.0)
            self._cy = min(max(float(py), 0.0), self._h - 1.0)
        self._ref, self._t, self._speed = (nx, ny), t, 0.0
        return self._cx, self._cy, True

    @staticmethod
    def _gain(speed: float) -> float:
        lo, hi = config.CV_SPEED_SLOW, config.CV_SPEED_FAST
        f = min(max((speed - lo) / (hi - lo), 0.0), 1.0)
        return config.CV_GAIN_SLOW + f * (config.CV_GAIN_FAST - config.CV_GAIN_SLOW)

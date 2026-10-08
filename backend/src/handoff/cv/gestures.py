"""Pinch -> mouse-button logic. Pure: no camera, no OS, no clock (the caller passes time).

A closed pinch presses the primary button, an open one releases it. A quick pinch is therefore
an ordinary click and a held pinch is an ordinary drag, which is what lets a hand drag a file
from the Desktop into the edge strip. There is deliberately no separate "click zone": the old
wide-gap band fired a click after every drop.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from handoff import config


@dataclass(frozen=True)
class HandSample:
    t: float  # capture time (seconds, monotonic)
    x: float  # target screen position in pixels, already smoothed
    y: float
    pinch: float  # thumb-index distance / hand size
    reacquired: bool = False  # the hand came back after being lost: jump, do not glide


@dataclass(frozen=True)
class Move:
    x: int
    y: int


@dataclass(frozen=True)
class Down:
    pass


@dataclass(frozen=True)
class Up:
    pass


@dataclass(frozen=True)
class CancelDrag:
    """Esc, then release: the hand was lost mid-drag, so the OS drag must not be dropped."""


@dataclass(frozen=True)
class Gesture:
    """Canonical `gesture_detected` for the UI (feedback only)."""

    name: str


Action = Move | Down | Up | CancelDrag | Gesture


class GestureMachine:
    def __init__(self) -> None:
        self.pressed = False
        self._release_count = 0
        self._cx = 0.0
        self._cy = 0.0
        self._placed = False
        self._last_t: float | None = None
        self._last_pos: tuple[int, int] | None = None
        self._down_at = (0.0, 0.0)
        self._dragged = False

    @property
    def mode(self) -> str:
        return "DRAG" if self.pressed else ""

    def step(self, now: float, sample: HandSample | None) -> list[Action]:
        out: list[Action] = []
        lost = sample is None or now - sample.t > config.CV_LOST_GRACE_SECONDS
        if lost or sample is None:
            self._last_t = None
            return self._on_lost()

        dt = max(now - self._last_t, 1e-4) if self._last_t is not None else 0.0
        self._last_t = now
        self._follow(sample, dt)
        pos = (round(self._cx), round(self._cy))
        if pos != self._last_pos:
            self._last_pos = pos
            out.append(Move(*pos))

        if not self.pressed:
            if sample.pinch < config.CV_PINCH_ON:
                self.pressed = True
                self._release_count = 0
                self._dragged = False
                self._down_at = (self._cx, self._cy)
                out += [Down(), Gesture("pinch_closed")]
        else:
            if math.hypot(self._cx - self._down_at[0], self._cy - self._down_at[1]) > (
                config.CV_DRAG_RADIUS_PX
            ):
                self._dragged = True
            if sample.pinch > config.CV_PINCH_OFF:
                self._release_count += 1
                if self._release_count >= config.CV_RELEASE_FRAMES:
                    self.pressed = False
                    out += [Up(), Gesture("pinch_open")]
            else:
                self._release_count = 0
        return out

    def _follow(self, s: HandSample, dt: float) -> None:
        if not self._placed or s.reacquired:
            self._cx, self._cy, self._placed = s.x, s.y, True
            return
        a = 1.0 - math.exp(-dt / config.CV_FOLLOW_TAU)
        self._cx += (s.x - self._cx) * a
        self._cy += (s.y - self._cy) * a

    def _on_lost(self) -> list[Action]:
        if not self.pressed:
            return []
        self.pressed = False
        self._release_count = 0
        # Esc only when a drag was under way; a plain held click should just be released.
        # (Esc in a focused dialog or editor would otherwise be a surprise.)
        if self._dragged:
            return [CancelDrag(), Gesture("pinch_open")]
        return [Up(), Gesture("pinch_open")]

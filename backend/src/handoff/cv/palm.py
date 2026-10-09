"""COPY gesture (ADR-057, ADR-058): open palm -> closed palm is a grab, closed -> open a release.

Pure: no camera, no OS, no clock (the caller passes time). A pose only counts once it has been
stable for CV_PALM_HOLD_SECONDS, so a relaxing hand does not fire it.

This machine does not know which laptop holds a grab. The laptop that grabs sees open -> closed;
the laptop the closed hand is carried to sees the hand arrive already closed and then open. Both
report `release` for a closed palm that opens (the hand may have been out of view for a while in
between); the core decides what that means.
"""

from __future__ import annotations

import math

from handoff import config
from handoff.cv.samples import POSE_FIST, POSE_OPEN

GRAB = "grab"
RELEASE = "release"


class PalmMachine:
    def __init__(self) -> None:
        self._pose: str | None = None
        self._since = 0.0
        self._open_seen: float | None = None  # last time an open palm was stable
        self._fist_seen: float | None = None  # last time a closed palm was stable
        self._cooldown_until = -math.inf
        self._last_now = -math.inf

    def step(self, now: float, pose: str | None) -> list[str]:
        if not math.isfinite(now) or now < self._last_now:
            return []
        self._last_now = now
        if pose != self._pose:
            self._pose, self._since = pose, now
        if pose not in (POSE_OPEN, POSE_FIST) or now - self._since < config.CV_PALM_HOLD_SECONDS:
            return []
        if now < self._cooldown_until:
            return []

        if pose == POSE_FIST:
            self._fist_seen = now
            if self._open_seen is not None and (
                now - self._open_seen <= config.CV_GRAB_WINDOW_SECONDS
            ):
                self._open_seen = None
                return [GRAB]
            return []
        if self._fist_seen is not None and now - self._fist_seen <= config.CV_HOLD_MAX_SECONDS:
            self._fist_seen = self._open_seen = None
            self._cooldown_until = now + config.CV_COPY_COOLDOWN_SECONDS
            return [RELEASE]
        self._open_seen = now
        return []

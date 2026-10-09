"""COPY gesture (ADR-057): open palm -> closed palm is a grab, closed -> open palm a release.

Pure: no camera, no OS, no clock (the caller passes time). A pose only counts once it has been
stable for CV_PALM_HOLD_SECONDS, so a relaxing hand does not fire it. While a grab is held the
hand may leave the camera's view (it is being carried to the other device) and still release.
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
        self._held_at: float | None = None
        self._cooldown_until = -math.inf
        self._last_now = -math.inf

    @property
    def holding(self) -> bool:
        return self._held_at is not None

    def step(self, now: float, pose: str | None) -> list[str]:
        if not math.isfinite(now) or now < self._last_now:
            return []
        self._last_now = now
        if self._held_at is not None and now - self._held_at > config.CV_HOLD_MAX_SECONDS:
            self._held_at = None  # nobody released it: the grab is gone
        if pose != self._pose:
            self._pose, self._since = pose, now
        if pose not in (POSE_OPEN, POSE_FIST) or now - self._since < config.CV_PALM_HOLD_SECONDS:
            return []

        if self._held_at is not None:
            if pose == POSE_OPEN:
                self._held_at, self._open_seen = None, None
                self._cooldown_until = now + config.CV_COPY_COOLDOWN_SECONDS
                return [RELEASE]
            return []
        if pose == POSE_OPEN:
            self._open_seen = now
            return []
        if (
            self._open_seen is not None
            and now - self._open_seen <= config.CV_GRAB_WINDOW_SECONDS
            and now >= self._cooldown_until
        ):
            self._held_at, self._open_seen = now, None
            return [GRAB]
        return []

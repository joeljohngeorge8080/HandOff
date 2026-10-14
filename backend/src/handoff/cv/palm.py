"""COPY gesture (ADR-057, ADR-058): open palm -> closed palm is a grab, closed -> open a release.

Pure: no camera, no OS, no clock (the caller passes time). A pose only counts once it has held
CV_PALM_AGREE of its hold window (CV_PALM_OPEN_HOLD_SECONDS / CV_PALM_FIST_HOLD_SECONDS), so a
hand that closes in passing does not fire it, and one misread frame does not restart the wait.

This machine does not know which laptop holds a grab. The laptop that grabs sees open -> closed;
the laptop the closed hand is carried to sees the hand arrive already closed and then open. Both
report `release` for a closed palm that opens (the hand may have been out of view for a while in
between); the core decides what that means.
"""

from __future__ import annotations

import math
from collections import deque

from handoff import config
from handoff.cv.samples import POSE_FIST, POSE_OPEN

GRAB = "grab"
RELEASE = "release"


class PalmMachine:
    def __init__(self) -> None:
        self._poses: deque[tuple[float, str | None]] = deque()  # (since, pose), on change only
        self._open_seen: float | None = None  # last time an open palm was stable
        self._fist_seen: float | None = None  # last time a closed palm was stable
        self._cooldown_until = -math.inf
        self._last_now = -math.inf

    def step(self, now: float, pose: str | None) -> list[str]:
        if not math.isfinite(now) or now < self._last_now:
            return []
        self._last_now = now
        pose = self._stable(now, pose)
        if pose is None:
            return []
        if now < self._cooldown_until:
            # The open hand of a release still counts as the start of the next grab, but no
            # fist is remembered: one that closes and opens within the cooldown is ignored.
            if pose == POSE_OPEN:
                self._open_seen = now
            return []

        if pose == POSE_FIST:
            self._fist_seen = now
            if self._open_seen is not None and (
                now - self._open_seen <= config.CV_GRAB_WINDOW_SECONDS
            ):
                self._open_seen = None
                return [GRAB]
            return []
        self._open_seen = now
        if self._fist_seen is not None and now - self._fist_seen <= config.CV_HOLD_MAX_SECONDS:
            self._fist_seen = None
            self._cooldown_until = now + config.CV_COPY_COOLDOWN_SECONDS
            return [RELEASE]
        return []

    def _stable(self, now: float, pose: str | None) -> str | None:
        """The open or fist pose that held most of its hold window, else None."""
        if not self._poses or self._poses[-1][1] != pose:
            self._poses.append((now, pose))
        longest = max(config.CV_PALM_OPEN_HOLD_SECONDS, config.CV_PALM_FIST_HOLD_SECONDS)
        while len(self._poses) > 1 and self._poses[1][0] <= now - longest:
            self._poses.popleft()  # the first entry is the one in force at the window's start
        for p, hold in (
            (POSE_OPEN, config.CV_PALM_OPEN_HOLD_SECONDS),
            (POSE_FIST, config.CV_PALM_FIST_HOLD_SECONDS),
        ):
            if self._held(now, p, hold) >= config.CV_PALM_AGREE * hold:
                return p
        return None

    def _held(self, now: float, pose: str, window: float) -> float:
        """Seconds of the last `window` spent in `pose`; 0 until a full window was watched."""
        start = now - window
        if self._poses[0][0] > start:
            return 0.0
        entries = list(self._poses)
        held = 0.0
        for (since, p), (until, _) in zip(entries, [*entries[1:], (now, None)], strict=True):
            if p == pose and until > start:
                held += until - max(since, start)
        return held

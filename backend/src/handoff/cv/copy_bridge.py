"""COPY gesture, core side (ADR-057): a validated `grab` reads the copied files, a `release` sends.

The worker only reports that the hand grabbed or released; it never names a file. The files come
from the OS clipboard (what Ctrl+C just copied) and go through the same `drop.send` pipeline as a
dragged drop: every type, size, symlink and executable check, all-or-nothing, one connected peer.
Feedback reaches the UI as ordinary `gesture_detected` events.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Any

from handoff import config
from handoff.cv.clipboard import read_clipboard_files
from handoff.errors import HandOffError
from handoff.events import EventBus

log = logging.getLogger(__name__)


class CopyBridge:
    def __init__(
        self,
        events: EventBus,
        send: Callable[[list[str]], Any],
        read: Callable[[], list[str]] = read_clipboard_files,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._events = events
        self._send = send
        self._read = read
        self._clock = clock
        self._lock = threading.Lock()  # a release waits for the grab that is still reading
        self._held: tuple[list[str], float] | None = None

    def _say(self, gesture: str) -> None:
        self._events.publish(
            "cv.event", {"event": "gesture_detected", "gesture": gesture, "confidence": 1.0}
        )

    def on_grab(self) -> None:
        with self._lock:
            self._held = None  # a new grab replaces any earlier one
            try:
                paths = self._read()
            except HandOffError as exc:
                log.warning("Copy gesture: %s", exc.message)
                self._say("copy_failed")
                return
            if not paths:
                self._say("copy_empty")
                return
            self._held = (paths, self._clock())
            self._say("copied")

    def on_release(self) -> None:
        with self._lock:
            held, self._held = self._held, None  # one release sends once
            if held is None or self._clock() - held[1] > config.CV_HOLD_MAX_SECONDS:
                return
            try:
                self._send(held[0])
            except HandOffError as exc:
                log.info("Copy gesture send refused: %s (%s)", exc.message, exc.code)
                self._say("send_failed")
            except Exception:
                log.exception("Copy gesture send failed")
                self._say("send_failed")
            else:
                self._say("sent")

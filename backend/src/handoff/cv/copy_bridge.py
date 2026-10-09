"""COPY gesture, core side (ADR-057, ADR-058): grab on one laptop, release on the other.

The laptop that grabs reads the files Ctrl+C just copied and *holds* them. It never sends on its
own release: an open palm there cancels the grab. The files move when the closed hand is carried to
the other laptop and opens in front of its camera: that laptop's `release` finds nothing held, so
it sends a signed *claim* to its connected peer (`POST /api/v1/handoff/claim`). The holder answers
a claim only from its trusted connected peer, only while the grab is still fresh, and then sends
through the same `drop.send` pipeline as a dragged drop (every type, size, symlink and executable
check, all-or-nothing). The worker never names a file; the clipboard is read only after a grab.
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
        claim: Callable[[], None],
        active_peer: Callable[[], str | None],
        read: Callable[[], list[str]] = read_clipboard_files,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._events = events
        self._send = send
        self._claim = claim
        self._active_peer = active_peer
        self._read = read
        self._clock = clock
        self._lock = threading.Lock()  # a claim waits for the grab that is still reading
        self._held: tuple[list[str], float] | None = None

    def _say(self, gesture: str) -> None:
        self._events.publish(
            "cv.event", {"event": "gesture_detected", "gesture": gesture, "confidence": 1.0}
        )

    def _live(self) -> list[str] | None:
        if self._held is None:
            return None
        paths, at = self._held
        return paths if self._clock() - at <= config.CV_HOLD_MAX_SECONDS else None

    # ----- this laptop's own gestures ----------------------------------------------------

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
            if self._live() is not None:
                self._held = None  # the grabbing laptop: opening the hand here cancels
                self._say("copy_cancelled")
                return
            self._held = None
        try:
            self._claim()
        except HandOffError as exc:
            log.info("Copy gesture claim refused: %s (%s)", exc.message, exc.code)
            self._say("claim_failed")
        except Exception:
            log.exception("Copy gesture claim failed")
            self._say("claim_failed")
        else:
            self._say("claimed")

    # ----- the peer's claim (called by the peer API) -------------------------------------

    def serve_claim(self, requester_device_id: str) -> dict[str, str]:
        with self._lock:
            if self._active_peer() != requester_device_id:
                raise HandOffError("DEVICE_NOT_FOUND", "No HandOff device connected")
            paths = self._live()
            if paths is None:
                self._held = None
                raise HandOffError("NOTHING_HELD", "The other device is not holding anything.")
            self._held = None  # one grab sends once, whatever the outcome
            try:
                result = self._send(paths)
            except HandOffError as exc:
                log.info("Copy gesture send refused: %s (%s)", exc.message, exc.code)
                self._say("send_failed")
                raise
            except Exception:
                log.exception("Copy gesture send failed")
                self._say("send_failed")
                raise HandOffError("INTERNAL_ERROR", "Could not send the held files.") from None
            self._say("sent")
            return {"transfer_id": str(result["transfer"]["transfer_id"])}

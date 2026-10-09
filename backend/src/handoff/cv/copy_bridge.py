"""COPY gesture, core side (ADR-057, ADR-058, ADR-060): grab on one laptop, release on the other.

The laptop that grabs reads what Ctrl+C just copied and *holds* it: the files on the clipboard,
or, when the clipboard holds a picture instead (a browser's "Copy image"), a temporary PNG written
from it. It never sends on its own release: an open palm there cancels the grab. The files move
when the closed hand is carried to the other laptop and opens in front of its camera: that
laptop's `release` finds nothing held, so it sends a signed *claim* to its connected peer
(`POST /api/v1/handoff/claim`). The holder answers a claim only from its trusted connected peer,
only while the grab is still fresh, and then sends through the same `drop.send` pipeline as a
dragged drop (every type, size, symlink and executable check, all-or-nothing). The worker never
names a file; the clipboard is read only after a grab.

While something is held the UI is told what it is (`hand.held`: names and a count, never paths)
so the strip can show it; the event is repeated, empty, whenever the grab ends.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Callable
from typing import Any

from handoff import config
from handoff.cv.clipboard import is_png, read_clipboard_files, read_clipboard_image, trim_png
from handoff.errors import HandOffError
from handoff.events import EventBus

log = logging.getLogger(__name__)

MAX_NAMES_SHOWN = 20
MAX_NAME_LENGTH = 120
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")

Cancel = Callable[[], None]


def _timer(delay: float, fn: Callable[[], None]) -> Cancel:
    t = threading.Timer(delay, fn)
    t.daemon = True
    t.start()
    return t.cancel


def display_names(paths: list[str]) -> dict[str, Any]:
    """What the UI may show: bare file names, cleaned and bounded. Never a path."""
    names = []
    for p in paths[:MAX_NAMES_SHOWN]:
        base = p.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
        names.append(_CONTROL.sub("", base)[:MAX_NAME_LENGTH] or "file")
    return {"names": names, "count": len(paths)}


class CopyBridge:
    def __init__(
        self,
        events: EventBus,
        send: Callable[[list[str]], Any],
        claim: Callable[[], None],
        active_peer: Callable[[], str | None],
        read: Callable[[], list[str]] = read_clipboard_files,
        clock: Callable[[], float] = time.monotonic,
        *,
        read_image: Callable[[], bytes | None] = read_clipboard_image,
        stash: Callable[[bytes], str] | None = None,
        discard: Callable[[str], None] = lambda _path: None,
        schedule: Callable[[float, Callable[[], None]], Cancel] = _timer,
    ) -> None:
        self._events = events
        self._send = send
        self._claim = claim
        self._active_peer = active_peer
        self._read = read
        self._clock = clock
        self._read_image = read_image
        self._stash = stash  # writes a picture to a temporary PNG; None = pictures unsupported
        self._discard = discard
        self._schedule = schedule
        self._lock = threading.Lock()  # a claim waits for the grab that is still reading
        self._held: tuple[list[str], float] | None = None
        self._temp: list[str] = []  # files we wrote ourselves; the user's own files never go here
        self._cancel_expiry: Cancel | None = None
        self._generation = 0

    def _say(self, gesture: str) -> None:
        self._events.publish(
            "cv.event", {"event": "gesture_detected", "gesture": gesture, "confidence": 1.0}
        )

    def _live(self) -> list[str] | None:
        if self._held is None:
            return None
        paths, at = self._held
        return paths if self._clock() - at <= config.CV_HOLD_MAX_SECONDS else None

    # ----- holding ------------------------------------------------------------------------

    def _forget(self) -> bool:
        """Drop whatever is held (and our temporary copies). True if something was held."""
        had = self._held is not None
        self._held = None
        self._generation += 1
        if self._cancel_expiry is not None:
            self._cancel_expiry()
            self._cancel_expiry = None
        temp, self._temp = self._temp, []
        for path in temp:
            try:
                self._discard(path)
            except Exception:
                log.exception("Could not remove a temporary copy")
        return had

    def _publish_held(self, paths: list[str] | None) -> None:
        data = display_names(paths) if paths else {"names": [], "count": 0}
        self._events.publish("hand.held", data)

    def _clear(self) -> None:
        if self._forget():
            self._publish_held(None)

    def _expire(self, generation: int) -> None:
        with self._lock:
            if generation == self._generation:
                self._clear()

    def _hold(self, paths: list[str], temp: list[str]) -> None:
        self._held = (paths, self._clock())
        self._temp = temp
        generation = self._generation
        self._cancel_expiry = self._schedule(
            config.CV_HOLD_MAX_SECONDS, lambda: self._expire(generation)
        )
        self._publish_held(paths)

    def _clipboard_contents(self) -> tuple[list[str], list[str]]:
        """(paths to hold, temporary files we created). Files win over a picture."""
        paths = self._read()
        if paths or self._stash is None:
            return paths, []
        image = self._read_image()
        if not image:
            return [], []
        image = trim_png(image)
        if not is_png(image):
            return [], []
        if len(image) > config.MAX_FILE_SIZE:
            raise HandOffError("FILE_TOO_LARGE", "The copied picture is too large to send.")
        path = self._stash(image)
        return [path], [path]

    # ----- this laptop's own gestures ----------------------------------------------------

    def on_grab(self) -> None:
        with self._lock:
            had = self._forget()  # a new grab replaces any earlier one
            try:
                paths, temp = self._clipboard_contents()
            except HandOffError as exc:
                log.warning("Copy gesture: %s", exc.message)
                if had:
                    self._publish_held(None)
                self._say("copy_failed")
                return
            if not paths:
                if had:
                    self._publish_held(None)
                self._say("copy_empty")
                return
            self._hold(paths, temp)
            self._say("copied")

    def on_release(self) -> None:
        with self._lock:
            if self._live() is not None:
                self._clear()  # the grabbing laptop: opening the hand here cancels
                self._say("copy_cancelled")
                return
            self._clear()  # an expired grab, if any
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
                self._clear()
                raise HandOffError("NOTHING_HELD", "The other device is not holding anything.")
            temp = list(self._temp)
            self._held, self._temp = None, []  # one grab sends once, whatever the outcome
            self._generation += 1
            if self._cancel_expiry is not None:
                self._cancel_expiry()
                self._cancel_expiry = None
            self._publish_held(None)
            try:
                result = self._send(paths)  # imports its own copies before it returns
            except HandOffError as exc:
                log.info("Copy gesture send refused: %s (%s)", exc.message, exc.code)
                self._say("send_failed")
                raise
            except Exception:
                log.exception("Copy gesture send failed")
                self._say("send_failed")
                raise HandOffError("INTERNAL_ERROR", "Could not send the held files.") from None
            finally:
                for path in temp:
                    try:
                        self._discard(path)
                    except Exception:
                        log.exception("Could not remove a temporary copy")
            self._say("sent")
            return {"transfer_id": str(result["transfer"]["transfer_id"])}

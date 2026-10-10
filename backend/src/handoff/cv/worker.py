"""The hand-control worker: `python -m handoff --cv-worker`, a child process of the core.

stdout carries JSON lines for the core's supervisor (status and canonical ADR-052 events);
logs go to stderr. The worker exits when its stdin closes, so it cannot outlive the core, and it
always releases the mouse button on the way out.
"""

from __future__ import annotations

import contextlib
import json
import logging
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import IO

from handoff import config
from handoff.cv.gestures import (
    Action,
    CancelDrag,
    Down,
    Gesture,
    GestureMachine,
    HandSample,
    Move,
    Up,
)
from handoff.cv.palm import GRAB, RELEASE, PalmMachine
from handoff.cv.pointer import PointerBackend, PyAutoGuiPointer
from handoff.cv.samples import SampleBuilder
from handoff.errors import HandOffError

log = logging.getLogger("handoff.cv")


class Emitter:
    """Writes protocol lines. Failures to write mean the core is gone: stop quietly."""

    def __init__(self, out: IO[str]) -> None:
        self._out = out
        self._lock = threading.Lock()
        self._last_status: tuple[str, str] | None = None
        self._last_status_at = 0.0

    def _write(self, obj: dict[str, object]) -> bool:
        line = json.dumps(obj, separators=(",", ":"))
        try:
            with self._lock:
                self._out.write(line + "\n")
                self._out.flush()
        except (OSError, ValueError):
            return False
        return True

    def status(self, state: str, message: str = "", code: str = "", now: float = 0.0) -> None:
        key = (state, message)
        repeat_after = config.CV_STATUS_INTERVAL_SECONDS
        if key == self._last_status and now - self._last_status_at < repeat_after:
            return
        self._last_status, self._last_status_at = key, now
        obj: dict[str, object] = {"event": "status", "state": state, "message": message}
        if code:
            obj["code"] = code
        self._write(obj)

    def gesture(self, name: str) -> None:
        self._write({"event": "gesture_detected", "gesture": name, "confidence": 1.0})

    def palm(self, name: str) -> None:
        """`grab` / `release` (ADR-052). The core, not this process, decides what they mean."""
        self._write({"event": name})


def _scroll_gain(speed: float) -> float:
    """Wheel steps per frame-height of fingertip movement at `speed` (frame-heights/second)."""
    lo, hi = config.CV_SCROLL_SPEED_SLOW, config.CV_SCROLL_SPEED_FAST
    f = min(max((speed - lo) / (hi - lo), 0.0), 1.0)
    slow, fast = config.CV_SCROLL_STEPS_SLOW, config.CV_SCROLL_STEPS_FAST
    return slow + f * (fast - slow)


class Controller:
    """Applies GestureMachine actions to the pointer. The only place that presses buttons."""

    def __init__(
        self,
        pointer: PointerBackend,
        emitter: Emitter,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.pointer = pointer
        self.emitter = emitter
        self.machine = GestureMachine()
        self.palm = PalmMachine()
        self._sleep = sleep
        self._scroll_t: float | None = None  # capture time of the last sample scrolled from
        self._scroll_y: float | None = None
        self._scroll_rest = 0.0  # fraction of a wheel step carried to the next frame
        self._scroll_prev_t: float | None = None

    def tick(self, now: float, sample: HandSample | None) -> None:
        self._apply(self.machine.step(now, sample))
        seen = sample is not None and now - sample.t <= config.CV_LOST_GRACE_SECONDS
        self._scroll(sample if seen else None)
        for event in self.palm.step(now, sample.pose if seen and sample else None):
            self._palm_event(event)

    def _scroll(self, sample: HandSample | None) -> None:
        """Turns the hand's height change into wheel steps, once per camera frame."""
        if sample is None or sample.scroll_y is None or self.machine.pressed:
            self._scroll_t = self._scroll_y = self._scroll_prev_t = None
            self._scroll_rest = 0.0
            return
        if sample.t == self._scroll_t:
            return  # the control loop runs faster than the camera: this frame was used
        self._scroll_t = sample.t
        prev, self._scroll_y = self._scroll_y, sample.scroll_y
        prev_t, self._scroll_prev_t = self._scroll_prev_t, sample.t
        if prev is None or prev_t is None or sample.reacquired or sample.t <= prev_t:
            self._scroll_rest = 0.0
            return
        # Image y grows downwards and a positive step scrolls up. Natural: hand up (dy < 0)
        # scrolls down, as two fingers on a touchpad move the content with them.
        sign = 1.0 if config.CV_SCROLL_NATURAL else -1.0
        dy = sample.scroll_y - prev
        self._scroll_rest += sign * dy * _scroll_gain(abs(dy) / (sample.t - prev_t))
        steps = int(self._scroll_rest)  # toward zero; the remainder carries over
        if steps:
            self._scroll_rest -= steps
            self.pointer.scroll(steps)

    def _palm_event(self, event: str) -> None:
        if event == GRAB:
            if self.machine.pressed:
                return  # a drag is in progress: Ctrl+C would copy the wrong thing
            self.emitter.gesture("palm_grab")  # feedback first: the clipboard read takes a moment
            self.pointer.copy()  # the file manager puts the selection on the clipboard
            self._sleep(config.CV_COPY_SETTLE_SECONDS)
        elif event == RELEASE:
            self.emitter.gesture("palm_release")
        if event in (GRAB, RELEASE):
            self.emitter.palm(event)

    def _apply(self, actions: list[Action]) -> None:
        for a in actions:
            if isinstance(a, Move):
                self.pointer.move(a.x, a.y)
            elif isinstance(a, Down):
                self.pointer.down()
            elif isinstance(a, Up):
                self.pointer.up()
            elif isinstance(a, CancelDrag):
                # Esc first: it cancels the OS drag, so the file is not dropped wherever the
                # cursor happens to be. Then release the button.
                self.pointer.escape()
                self.pointer.up()
            elif isinstance(a, Gesture):
                self.emitter.gesture(a.name)

    def shutdown(self) -> None:
        if self.machine.pressed:
            self.machine.pressed = False
            self.pointer.up()


def run(
    model_path: Path,
    out: IO[str],
    stdin: IO[str],
    pointer: PointerBackend | None = None,
    scroll: bool = False,
) -> int:
    emitter = Emitter(out)
    emitter.status("starting")
    tracker = None
    try:
        pointer = pointer or PyAutoGuiPointer()
        from handoff.cv.tracker import HandTracker

        tracker = HandTracker(model_path)
    except HandOffError as exc:
        emitter.status("error", exc.message, exc.code)
        return 2

    stop = threading.Event()
    latest: list[HandSample | None] = [None]
    lock = threading.Lock()
    builder = SampleBuilder(pointer.screen_size(), pointer.position, scroll=scroll)

    def watch_stdin() -> None:
        with contextlib.suppress(OSError, ValueError):
            stdin.read()  # blocks until the core closes the pipe or dies
        stop.set()

    def track() -> None:
        try:
            while not stop.is_set():
                got = tracker.read()
                if got is None:
                    time.sleep(0.005)
                    continue
                t, det = got
                if det is not None:
                    sample = builder.build(t, det)
                    with lock:
                        latest[0] = sample
        except Exception:
            log.exception("hand tracking stopped")
            emitter.status("error", "Hand tracking stopped unexpectedly.", "CV_UNAVAILABLE")
            stop.set()

    threading.Thread(target=watch_stdin, daemon=True).start()
    tracker_thread = threading.Thread(target=track, daemon=True)
    tracker_thread.start()

    ctl = Controller(pointer, emitter)
    period = 1.0 / config.CV_CONTROL_HZ
    try:
        while not stop.is_set():
            started = time.perf_counter()
            with lock:
                sample = latest[0]
            ctl.tick(started, sample)
            seen = sample is not None and started - sample.t <= config.CV_LOST_GRACE_SECONDS
            emitter.status("tracking" if seen else "no_hand", now=started)
            rest = period - (time.perf_counter() - started)
            if rest > 0:
                time.sleep(rest)
    finally:
        stop.set()
        ctl.shutdown()
        tracker_thread.join(timeout=2)
        tracker.close()
    return 0


def main(argv: list[str] | None = None, scroll: bool = False) -> int:
    logging.basicConfig(stream=sys.stderr, level=logging.INFO)
    model = Path(argv[0]) if argv else Path(config.CV_MODEL_FILENAME)
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.winmm.timeBeginPeriod(1)  # type: ignore[attr-defined,unused-ignore]
    try:
        return run(model, sys.stdout, sys.stdin, scroll=scroll)
    finally:
        if sys.platform == "win32":
            ctypes.windll.winmm.timeEndPeriod(1)  # type: ignore[attr-defined,unused-ignore]

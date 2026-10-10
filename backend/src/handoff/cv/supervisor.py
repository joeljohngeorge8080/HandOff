"""Runs the hand-control worker as a child process and relays its output (ADR-056).

The worker is untrusted input: its lines are size-capped, parsed strictly, and only a fixed set
of canonical ADR-052 events is passed on, rate limited. Nothing the worker says can start a
transfer; it can only change the status shown in the panel or add UI feedback.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import IO, Any

from handoff import config
from handoff.cv import pointer as pointer_mod
from handoff.errors import HandOffError
from handoff.events import EventBus

log = logging.getLogger(__name__)

STATES = frozenset({"off", "starting", "tracking", "no_hand", "error"})
ERROR_CODES = frozenset({"CV_UNAVAILABLE", "CV_UNSUPPORTED_SESSION", "CV_CAMERA_UNAVAILABLE"})
FORWARDED_EVENTS = frozenset({"gesture_detected", "direction_detected"})
PALM_EVENTS = frozenset({"grab", "release"})  # acted on by the core's CopyBridge, never forwarded
DIRECTIONS = frozenset({"left", "right", "up", "down"})
_NAME = re.compile(r"[a-z_]{1,32}")
MAX_MESSAGE = 200


def model_path() -> Path:
    """Bundled next to the executable when frozen; backend/models/ when run from source."""
    base = getattr(sys, "_MEIPASS", None)
    root = Path(base) if base else Path(__file__).resolve().parents[3] / "models"
    return root / config.CV_MODEL_FILENAME


def default_command(model: Path) -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "--cv-worker", str(model)]
    return [sys.executable, "-m", "handoff", "--cv-worker", str(model)]


def _release_button() -> None:
    """Best effort: a worker that died while pressing leaves the X/Windows button held down."""
    try:
        pointer_mod.load_pyautogui().mouseUp()
    except Exception:
        log.warning("Could not release the mouse button after the hand-control worker died")


class CvSupervisor:
    def __init__(
        self,
        events: EventBus,
        *,
        command: Callable[[Path], Sequence[str]] = default_command,
        model: Callable[[], Path] = model_path,
        preflight: Callable[[], None] = pointer_mod.check_session,
        release_button: Callable[[], None] = _release_button,
        clock: Callable[[], float] = time.monotonic,
        scroll: Callable[[], bool] = lambda: False,
    ) -> None:
        self._events = events
        self._command = command
        self._model = model
        self._preflight = preflight
        self._release_button = release_button
        self._clock = clock
        self._scroll = scroll
        self._lock = threading.RLock()
        self._proc: subprocess.Popen[str] | None = None
        self._stopping = False
        self._state: dict[str, str] = {"state": "off", "message": ""}
        self._window_start = 0.0
        self._window_count = 0
        self.on_grab: Callable[[], None] | None = None  # set by Network once sending is possible
        self.on_release: Callable[[], None] | None = None

    # ----- public ---------------------------------------------------------------------

    def status(self) -> dict[str, str]:
        with self._lock:
            return dict(self._state)

    def start(self) -> dict[str, str]:
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                return self.status()
            model = self._model()
            try:
                self._preflight()
                if not model.is_file():
                    hint = (
                        "Reinstall HandOff."
                        if getattr(sys, "frozen", False)
                        else "Run: python scripts/fetch_hand_model.py"
                    )
                    raise HandOffError(
                        "CV_UNAVAILABLE", f"The hand-tracking model is missing. {hint}"
                    )
            except HandOffError as exc:
                self._set("error", exc.message, exc.code)
                return self.status()
            self._stopping = False
            self._set("starting")
            try:
                cmd = [*self._command(model), *(["--cv-scroll"] if self._scroll() else [])]
                self._proc = self._spawn(cmd)
            except OSError as exc:
                self._set("error", f"Could not start hand control: {exc}", "CV_UNAVAILABLE")
                return self.status()
            threading.Thread(
                target=self._read, args=(self._proc,), daemon=True, name="cv-reader"
            ).start()
            return self.status()

    def stop(self) -> dict[str, str]:
        with self._lock:
            proc, self._proc = self._proc, None
            self._stopping = True
        if proc is not None:
            self._terminate(proc)
        with self._lock:
            self._set("off")
            return self.status()

    def restart(self) -> dict[str, str]:
        """Stop and start again, so the worker picks up a changed switch (scroll)."""
        self.stop()
        return self.start()

    # ----- internals ------------------------------------------------------------------

    @staticmethod
    def _spawn(cmd: Sequence[str]) -> subprocess.Popen[str]:
        flags = 0x0800_0000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW
        return subprocess.Popen(  # noqa: S603 - our own executable, no shell
            list(cmd),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=None,
            text=True,
            creationflags=flags,
        )

    @staticmethod
    def _terminate(proc: subprocess.Popen[str]) -> None:
        try:
            if proc.stdin:
                proc.stdin.close()  # EOF = "stop": the worker releases the button and exits
            proc.wait(timeout=3)
        except (subprocess.TimeoutExpired, OSError):
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()

    def _set(self, state: str, message: str = "", code: str = "") -> None:
        self._state = {"state": state, "message": message}
        data = dict(self._state)
        if code:
            data["code"] = code
            self._state["code"] = code
        self._events.publish("cv.status", data)

    def _read(self, proc: subprocess.Popen[str]) -> None:
        out: IO[str] | None = proc.stdout
        if out is None:
            return
        while True:
            line = out.readline(config.CV_MAX_LINE_BYTES + 1)
            if not line:
                break
            if len(line) > config.CV_MAX_LINE_BYTES and not line.endswith("\n"):
                while (rest := out.readline(config.CV_MAX_LINE_BYTES)) and not rest.endswith("\n"):
                    pass
                continue  # oversized: dropped
            self.handle_line(line)
        rc = proc.wait()
        with self._lock:
            if self._proc is not proc or self._stopping:
                return  # a normal stop()
            self._proc = None
            self._release_button()
            self._set("error", "Hand control stopped unexpectedly.", "CV_UNAVAILABLE")
            log.warning("Hand-control worker exited with code %s", rc)

    def handle_line(self, line: str) -> None:
        """Validate one worker line; unknown or malformed input is dropped, never trusted."""
        try:
            msg: Any = json.loads(line)
        except ValueError:
            return
        if not isinstance(msg, dict):
            return
        name = msg.get("event")
        if name == "status":
            self._on_status(msg)
        elif name in FORWARDED_EVENTS:
            self._on_event(name, msg)
        elif name in PALM_EVENTS:
            self._on_palm(name)

    def _on_status(self, msg: dict[str, Any]) -> None:
        state, text, code = msg.get("state"), msg.get("message", ""), msg.get("code", "")
        if state not in STATES or state == "off" or not isinstance(text, str):
            return
        if state == "error" and code not in ERROR_CODES:
            code = "CV_UNAVAILABLE"
        with self._lock:
            if self._proc is None or self._stopping:
                return
            if state == "error":
                self._set("error", text[:MAX_MESSAGE], str(code))
            elif (state, text) != (self._state["state"], self._state["message"]):
                self._set(state, text[:MAX_MESSAGE])

    def _on_palm(self, name: str) -> None:
        """The hand grabbed or released (ADR-057). The worker names no file: the handler reads
        the clipboard itself. Run off the reader thread, so a slow import never stalls status."""
        with self._lock:
            if self._proc is None or self._stopping:
                return
            handler = self.on_grab if name == "grab" else self.on_release
        if handler is not None:
            threading.Thread(
                target=self._run_handler, args=(handler,), daemon=True, name=f"cv-{name}"
            ).start()

    @staticmethod
    def _run_handler(handler: Callable[[], None]) -> None:
        try:
            handler()
        except Exception:
            log.exception("Hand-control %s handler failed", getattr(handler, "__name__", "?"))

    def _on_event(self, name: str, msg: dict[str, Any]) -> None:
        data: dict[str, Any] = {"event": name}
        if name == "gesture_detected":
            gesture, conf = msg.get("gesture"), msg.get("confidence")
            if not isinstance(gesture, str) or not _NAME.fullmatch(gesture):
                return
            if isinstance(conf, bool) or not isinstance(conf, int | float) or not 0 <= conf <= 1:
                return
            data.update(gesture=gesture, confidence=float(conf))
        else:
            direction = msg.get("direction")
            if direction not in DIRECTIONS:
                return
            data["direction"] = direction
        now = self._clock()
        with self._lock:
            if now - self._window_start >= 1.0:
                self._window_start, self._window_count = now, 0
            if self._window_count >= config.CV_MAX_EVENTS_PER_SEC:
                return
            self._window_count += 1
        self._events.publish("cv.event", data)

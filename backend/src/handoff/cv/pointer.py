"""The OS pointer. The only module that synthesises input; everything else asks it to."""

from __future__ import annotations

import os
import sys
from typing import Any, Protocol

from handoff.errors import HandOffError


class PointerBackend(Protocol):
    def screen_size(self) -> tuple[int, int]: ...
    def position(self) -> tuple[int, int]: ...
    def move(self, x: int, y: int) -> None: ...
    def down(self) -> None: ...
    def up(self) -> None: ...
    def escape(self) -> None: ...
    def copy(self) -> None: ...


def check_session() -> None:
    """Input injection works on X11/XWayland and Windows, not on a native Wayland session."""
    if sys.platform.startswith("linux") and not os.environ.get("DISPLAY"):
        raise HandOffError(
            "CV_UNSUPPORTED_SESSION",
            "Hand control needs an X11 session (or XWayland); none was found.",
        )


def load_pyautogui() -> Any:
    """Import pyautogui without letting it take the process down.

    pyautogui imports `mouseinfo`, which calls sys.exit() when tkinter is missing (it is not
    bundled). HandOff never uses MouseInfo, so block that import: pyautogui then falls back
    cleanly. SystemExit is caught as well, because nothing may exit the core or the worker.
    """
    sys.modules.setdefault("mouseinfo", None)  # type: ignore[arg-type]
    try:
        import pyautogui
    except (Exception, SystemExit) as exc:  # no display, missing Xlib, ...
        raise HandOffError("CV_UNAVAILABLE", f"Cannot control the pointer: {exc}") from exc
    return pyautogui


class PyAutoGuiPointer:
    def __init__(self) -> None:
        check_session()
        pyautogui = load_pyautogui()
        pyautogui.PAUSE = 0
        pyautogui.FAILSAFE = False  # a hand at the screen corner must not abort the worker
        self._g: Any = pyautogui

    def screen_size(self) -> tuple[int, int]:
        w, h = self._g.size()
        return int(w), int(h)

    def position(self) -> tuple[int, int]:
        p = self._g.position()
        return int(p[0]), int(p[1])

    def move(self, x: int, y: int) -> None:
        self._g.moveTo(x, y)

    def down(self) -> None:
        self._g.mouseDown()

    def up(self) -> None:
        self._g.mouseUp()

    def escape(self) -> None:
        self._g.press("esc")

    def copy(self) -> None:
        self._g.hotkey("ctrl", "c")

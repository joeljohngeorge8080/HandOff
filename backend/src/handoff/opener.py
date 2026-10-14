"""Open a received file with the OS default app (ADR-061).

This hands the file to the user's own viewer, the same as double-clicking it. It never runs
the file: only the allowed data types are opened, never a symlink or a non-regular file, and
the launcher gets an argument list (no shell). The call does not wait for the viewer.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from handoff.config import ALLOWED_EXTENSIONS
from handoff.errors import HandOffError


def _refuse(message: str) -> HandOffError:
    return HandOffError("OPEN_FAILED", message)


def _launch(path: Path) -> None:
    if sys.platform == "win32":
        os.startfile(str(path))  # type: ignore[attr-defined]  # noqa: S606
        return
    tool = "open" if sys.platform == "darwin" else "xdg-open"
    subprocess.Popen(  # noqa: S603
        [tool, str(path)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def open_with_default_app(path: Path) -> None:
    """Open `path`, or raise OPEN_FAILED. The caller decides whether that matters."""
    if path.suffix.lower() not in ALLOWED_EXTENSIONS:
        raise _refuse("This file type is not opened automatically.")
    if path.is_symlink() or not path.is_file():
        raise _refuse("Only regular files are opened automatically.")
    try:
        _launch(path)
    except OSError as exc:
        raise _refuse("No app could open the file.") from exc

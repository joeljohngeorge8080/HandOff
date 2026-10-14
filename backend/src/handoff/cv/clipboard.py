"""The files currently on the OS clipboard (ADR-057): what a grab (Ctrl+C) just copied.

The clipboard is untrusted input. This module only turns it into candidate absolute local paths;
`DropService` then applies every normal check (type, size, symlinks, executables) before anything
is imported.
"""

from __future__ import annotations

import os
import re
import sys
import threading
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

from handoff import config
from handoff.errors import HandOffError

_LIMIT = config.MAX_FILES_PER_TRANSFER + 1  # one over, so the drop pipeline can reject the excess
CF_HDROP = 15


def _unavailable(message: str) -> HandOffError:
    return HandOffError("CLIPBOARD_UNAVAILABLE", message)


def parse_uri_list(text: str) -> list[str]:
    """`text/uri-list`: one URI per line, `#` comments. Only local `file:` URIs are kept."""
    out: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parsed = urlparse(line)
        if parsed.scheme != "file" or parsed.netloc not in ("", "localhost"):
            continue
        if "\x00" in unquote(parsed.path):
            continue
        path = url2pathname(parsed.path)
        if not path or "\x00" in path or not os.path.isabs(path) or path in out:
            continue
        out.append(path)
        if len(out) >= _LIMIT:
            break
    return out


_HEX_BYTES = re.compile(r"(?:0x[0-9a-fA-F]{1,2}(?:\s+|$))+")


def decode_selection(text: str) -> str:
    """Tk hands non-text clipboard formats back as "0x66 0x69 ..." (the raw bytes in hex)."""
    if not _HEX_BYTES.fullmatch(text.strip() + " "):
        return text
    try:
        return bytes(int(h, 16) for h in text.split()).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return ""


def parse_gnome_copied(text: str) -> list[str]:
    """`x-special/gnome-copied-files`: a `copy`/`cut` line, then `text/uri-list` lines."""
    lines = text.splitlines()
    if lines and lines[0].strip() in ("copy", "cut"):
        lines = lines[1:]
    return parse_uri_list("\n".join(lines))


def read_clipboard_files() -> list[str]:
    """Candidate file paths on the clipboard; [] when it holds no files."""
    if sys.platform == "win32":
        return _read_windows()
    return _read_x11()


def _read_x11() -> list[str]:
    """tkinter (ships with Python) reads the X selection. Tk must stay on the thread that made
    it, so it gets a thread of its own, and a timeout so a stuck clipboard owner cannot hang us."""
    result: list[list[str] | HandOffError] = []

    def work() -> None:
        try:
            import tkinter
        except ImportError:
            result.append(_unavailable("Copying files needs tkinter, which is not installed."))
            return
        try:
            root = tkinter.Tk()
        except tkinter.TclError as exc:
            result.append(_unavailable(f"Could not open the clipboard: {exc}"))
            return
        try:
            root.withdraw()
            for target, parse in (
                ("text/uri-list", parse_uri_list),
                ("x-special/gnome-copied-files", parse_gnome_copied),
            ):
                try:
                    text = root.selection_get(selection="CLIPBOARD", type=target)  # type: ignore[no-untyped-call,unused-ignore]
                except tkinter.TclError:
                    continue  # that format is not on the clipboard
                if files := parse(decode_selection(str(text))):
                    result.append(files)
                    return
            result.append([])
        finally:
            root.destroy()

    t = threading.Thread(target=work, daemon=True, name="clipboard-read")
    t.start()
    t.join(config.CV_CLIPBOARD_TIMEOUT_SECONDS)
    if not result:
        raise _unavailable("The clipboard did not answer in time.")
    got = result[0]
    if isinstance(got, HandOffError):
        raise got
    return got


def _read_windows() -> list[str]:  # pragma: no cover - exercised on Windows only
    import ctypes
    import time
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)  # type: ignore[attr-defined,unused-ignore]
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)  # type: ignore[attr-defined,unused-ignore]
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.GetClipboardData.argtypes = [wintypes.UINT]
    user32.GetClipboardData.restype = wintypes.HANDLE
    user32.CloseClipboard.restype = wintypes.BOOL
    shell32.DragQueryFileW.argtypes = [
        wintypes.HANDLE,
        wintypes.UINT,
        wintypes.LPWSTR,
        wintypes.UINT,
    ]
    shell32.DragQueryFileW.restype = wintypes.UINT

    for _ in range(10):  # another program may hold the clipboard for a moment
        if user32.OpenClipboard(None):
            break
        time.sleep(0.02)
    else:
        raise _unavailable("The clipboard is busy.")
    try:
        handle = user32.GetClipboardData(CF_HDROP)
        if not handle:
            return []
        count = min(int(shell32.DragQueryFileW(handle, 0xFFFFFFFF, None, 0)), _LIMIT)
        paths: list[str] = []
        for i in range(count):
            length = int(shell32.DragQueryFileW(handle, i, None, 0))
            buf = ctypes.create_unicode_buffer(length + 1)
            shell32.DragQueryFileW(handle, i, buf, length + 1)
            if buf.value:
                paths.append(buf.value)
        return paths
    finally:
        user32.CloseClipboard()

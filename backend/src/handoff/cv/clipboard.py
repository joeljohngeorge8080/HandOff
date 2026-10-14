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
from collections.abc import Callable
from typing import Any
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

from handoff import config
from handoff.errors import HandOffError

_LIMIT = config.MAX_FILES_PER_TRANSFER + 1  # one over, so the drop pipeline can reject the excess
CF_HDROP = 15
# Tk returns binary formats as hex text ("0x89 " is 5 characters per byte): refuse a dump that
# could not possibly be an allowed file, rather than decode hundreds of megabytes.
MAX_IMAGE_DUMP = config.MAX_FILE_SIZE * 5 + 1024


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


def decode_selection_bytes(text: str) -> bytes | None:
    """Tk hands non-text clipboard formats back as "0x66 0x69 ..." (the raw bytes in hex)."""
    if not text or not _HEX_BYTES.fullmatch(text.strip() + " "):
        return None
    try:
        return bytes(int(h, 16) for h in text.split())
    except ValueError:
        return None


def decode_selection(text: str) -> str:
    raw = decode_selection_bytes(text)
    if raw is None:
        return text
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return ""


_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


_PNG_END = b"IEND\xaeB`\x82"


def trim_png(data: bytes) -> bytes:
    """Cut anything after the PNG's final chunk (a clipboard allocation can be padded)."""
    i = data.find(_PNG_END)
    return data[: i + len(_PNG_END)] if i >= 0 else data


def is_png(data: bytes) -> bool:
    """A real PNG starts with the 8-byte signature (and has at least a header after it)."""
    return len(data) > len(_PNG_SIGNATURE) + 8 and data.startswith(_PNG_SIGNATURE)


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


def read_clipboard_image() -> bytes | None:
    """A picture on the clipboard (what a browser copies for "Copy image") as PNG bytes, or None.
    Not validated here: the caller checks the signature and the size."""
    if sys.platform == "win32":
        return _read_windows_image()
    return _read_x11_image()


def _with_tk[T](read: Callable[[Any], T]) -> T:
    """Run `read(root)` against a hidden Tk root. Tk must stay on the thread that made it, so it
    gets a thread of its own, and a timeout so a stuck clipboard owner cannot hang us."""
    result: list[T | HandOffError] = []

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
            result.append(read(root))
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


def _selection(root: Any, target: str) -> str | None:
    import tkinter

    try:
        return str(
            root.selection_get(selection="CLIPBOARD", type=target)  # type: ignore[no-untyped-call,unused-ignore]
        )
    except tkinter.TclError:
        return None  # that format is not on the clipboard


def _files_from(root: Any) -> list[str]:
    for target, parse in (
        ("text/uri-list", parse_uri_list),
        ("x-special/gnome-copied-files", parse_gnome_copied),
    ):
        text = _selection(root, target)
        if text is not None and (files := parse(decode_selection(text))):
            return files
    return []


def _image_from(root: Any) -> bytes | None:
    text = _selection(root, "image/png")
    if text is None or len(text) > MAX_IMAGE_DUMP:
        return None  # absent, or far larger than any file HandOff would send
    return decode_selection_bytes(text)


def _read_x11() -> list[str]:
    return _with_tk(_files_from)


def _read_x11_image() -> bytes | None:
    return _with_tk(_image_from)


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


def _read_windows_image() -> bytes | None:  # pragma: no cover - exercised on Windows only
    """Browsers put a copied image on the clipboard in the registered "PNG" format."""
    import ctypes
    import time
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)  # type: ignore[attr-defined,unused-ignore]
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined,unused-ignore]
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
    user32.RegisterClipboardFormatW.restype = wintypes.UINT
    user32.GetClipboardData.argtypes = [wintypes.UINT]
    user32.GetClipboardData.restype = wintypes.HANDLE
    user32.CloseClipboard.restype = wintypes.BOOL
    kernel32.GlobalSize.argtypes = [wintypes.HANDLE]
    kernel32.GlobalSize.restype = ctypes.c_size_t
    kernel32.GlobalLock.argtypes = [wintypes.HANDLE]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [wintypes.HANDLE]

    for _ in range(10):  # another program may hold the clipboard for a moment
        if user32.OpenClipboard(None):
            break
        time.sleep(0.02)
    else:
        raise _unavailable("The clipboard is busy.")
    try:
        fmt = user32.RegisterClipboardFormatW("PNG")
        handle = user32.GetClipboardData(fmt) if fmt else None
        if not handle:
            return None
        size = int(kernel32.GlobalSize(handle))
        if size <= 0 or size > config.MAX_FILE_SIZE + 1024:
            return None
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            return None
        try:
            return ctypes.string_at(ptr, size)
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()

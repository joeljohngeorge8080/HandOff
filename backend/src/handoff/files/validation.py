"""File validation shared by import (local) and receive (network input, untrusted).

One strict filename rule is used everywhere: names with separators, traversal,
reserved Windows device names or invalid characters are rejected, never "fixed".
Files are stored under UUID names, so a display name never becomes a filesystem path.
"""

from __future__ import annotations

import os
import re
from typing import Any

from handoff.config import ALLOWED_EXTENSIONS, MAX_FILE_SIZE, MAX_FILENAME_BYTES
from handoff.errors import HandOffError

_RESERVED = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{i}" for i in range(1, 10)}
    | {f"lpt{i}" for i in range(1, 10)}
)
_INVALID_CHARS = frozenset('<>:"|?*')
_DRIVE = re.compile(r"^[A-Za-z]:")


def normalize_extension(name: str) -> str:
    """Lower-cased extension including the dot ('' when there is none)."""
    return os.path.splitext(name)[1].lower()


def validate_filename(name: str) -> str:
    if not isinstance(name, str):
        raise HandOffError("INVALID_FILE", "File name must be a string.")
    if not name.strip():
        raise HandOffError("INVALID_FILE", "File name is empty.")
    if "/" in name or "\\" in name or name in (".", "..") or _DRIVE.match(name):
        raise HandOffError(
            "INVALID_PATH", "File name must not contain a path.", {"file_name": name[:80]}
        )
    if any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise HandOffError("INVALID_FILE", "File name contains control characters.")
    if any(c in _INVALID_CHARS for c in name):
        raise HandOffError("INVALID_FILE", "File name contains invalid characters.")
    if name.endswith((".", " ")):
        raise HandOffError("INVALID_FILE", "File name must not end with a dot or space.")
    if len(name.encode("utf-8")) > MAX_FILENAME_BYTES:
        raise HandOffError("INVALID_FILE", "File name is too long.")
    if name.split(".")[0].rstrip(" ").casefold() in _RESERVED:
        raise HandOffError("INVALID_FILE", "File name is a reserved system name.")
    return name


def validate_extension(name: str) -> str:
    ext = normalize_extension(name)
    if ext not in ALLOWED_EXTENSIONS:
        raise HandOffError(
            "FILE_TYPE_NOT_SUPPORTED",
            "This file type is not supported in Phase 1.",
            {"file_name": name[:80], "extension": ext, "allowed": sorted(ALLOWED_EXTENSIONS)},
        )
    return ext


def validate_size(size: int, file_name: str | None = None) -> None:
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise HandOffError("INVALID_FILE", "File size is invalid.")
    if size > MAX_FILE_SIZE:
        details: dict[str, Any] = {"size": size, "maximum_size": MAX_FILE_SIZE}
        if file_name:
            details["file_name"] = file_name[:80]
        raise HandOffError("FILE_TOO_LARGE", "The file exceeds the 50 MB Phase-1 limit.", details)

"""Receiver-chosen destination folder (ADR-055, SECURITY §29).

The folder belongs to the receiving machine and is chosen by its own user. It is validated
by the receiver only; no network input ever supplies or influences a path. Files are written
with exclusive create so an existing file can never be overwritten, and each file's SHA-256
is verified again while it is copied into place.
"""

from __future__ import annotations

import hashlib
import logging
import os
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import BinaryIO

import platformdirs

from handoff.config import IO_CHUNK_SIZE
from handoff.errors import HandOffError
from handoff.files.validation import validate_filename

log = logging.getLogger(__name__)

MAX_NAME_ATTEMPTS = 10_000


def default_receive_dir() -> Path:
    """The OS Desktop folder (never a hard-coded user name); the home folder if it is missing."""
    try:
        desktop = Path(platformdirs.user_desktop_dir())
        if desktop.is_dir():
            return desktop
    except (OSError, RuntimeError, ValueError):
        log.warning("Could not determine the Desktop folder; using the home folder.")
    return Path.home()


def _invalid(message: str) -> HandOffError:
    return HandOffError("INVALID_PATH", message)


def validate_receive_dir(raw: object, *, forbidden: Iterable[Path] = ()) -> Path:
    """Return the resolved destination, or raise INVALID_PATH.

    It must be an absolute path to an existing, writable directory that is not inside any
    `forbidden` root (HandOff's own data directory, which holds the private key).
    """
    if not isinstance(raw, str) or not raw.strip() or "\x00" in raw:
        raise _invalid("The destination folder is not a valid path.")
    path = Path(raw)
    if not path.is_absolute():
        raise _invalid("The destination folder must be an absolute path.")
    if ".." in path.parts:
        raise _invalid("The destination folder must not contain '..'.")
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise _invalid("The destination folder does not exist.") from exc
    if not resolved.is_dir():
        raise _invalid("The destination is not a folder.")
    for root in forbidden:
        try:
            if resolved.is_relative_to(root.resolve()):
                raise _invalid("The destination folder cannot be inside HandOff's own data.")
        except OSError:
            continue
    try:
        with tempfile.TemporaryFile(dir=resolved):
            pass
    except OSError as exc:
        raise _invalid("The destination folder is not writable.") from exc
    return resolved


def _candidate(name: str, n: int) -> str:
    if n == 0:
        return name
    stem, ext = os.path.splitext(name)
    return f"{stem}({n}){ext}"


def reserve_unique_path(directory: Path, name: str) -> tuple[Path, BinaryIO]:
    """Atomically create `name` (or `name(1)`, `name(2)`, ...) in `directory`.

    Exclusive create means two racing writers can never get the same file, and an existing
    file is never opened. The caller owns the returned handle.
    """
    validate_filename(name)
    for n in range(MAX_NAME_ATTEMPTS + 1):
        target = directory / _candidate(name, n)
        if target.parent != directory:  # defence in depth; validate_filename forbids separators
            raise HandOffError("INVALID_PATH", "File name must not contain a path.")
        try:
            return target, target.open("xb")
        except FileExistsError:
            continue
        except OSError as exc:
            raise HandOffError(
                "FILE_STORAGE_ERROR", "The file could not be created in the destination folder."
            ) from exc
    raise HandOffError("FILE_STORAGE_ERROR", "No free file name was found in the destination.")


def copy_verified(src: Path, directory: Path, name: str, expected_sha256: str) -> Path:
    """Copy `src` into `directory` under a unique name, verifying SHA-256 on the way.

    On any failure only the file created by this call is removed; files that were already
    in the folder are never touched.
    """
    target, out = reserve_unique_path(directory, name)
    try:
        h = hashlib.sha256()
        try:
            with out, src.open("rb") as fin:
                while chunk := fin.read(IO_CHUNK_SIZE):
                    h.update(chunk)
                    out.write(chunk)
                out.flush()
                os.fsync(out.fileno())
        except OSError as exc:
            raise HandOffError(
                "FILE_STORAGE_ERROR", "The file could not be written to the destination."
            ) from exc
        if h.hexdigest() != expected_sha256:
            raise HandOffError("INVALID_HASH", "SHA-256 verification failed.")
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    return target

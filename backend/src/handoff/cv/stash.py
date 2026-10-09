"""Where a picture copied from a browser waits while it is held (ADR-060).

A browser's "Copy image" puts a picture on the clipboard, not a file. To send it like any other
file it is written as a PNG into HandOff's own scratch directory, then goes through the normal
`drop.send` checks (which copy it into managed storage). The names are generated here, never taken
from the clipboard, files are created exclusively (never overwritten) with user-only permissions,
and `discard` only ever deletes files that live in this directory.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path


class PictureStash:
    def __init__(
        self, directory: Path, clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    ) -> None:
        self._dir = directory
        self._clock = clock

    def save(self, png: bytes) -> str:
        self._dir.mkdir(parents=True, exist_ok=True)
        stem = f"image-{self._clock():%Y%m%d-%H%M%S}"
        for n in range(1000):
            name = f"{stem}.png" if n == 0 else f"{stem}-{n}.png"
            path = self._dir / name
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                continue
            with os.fdopen(fd, "wb") as out:
                out.write(png)
            return str(path)
        raise FileExistsError("Could not find a free name for the picture.")

    def discard(self, path: str) -> None:
        target = Path(path)
        try:
            inside = target.resolve().parent == self._dir.resolve()
        except OSError:
            return
        if inside:
            target.unlink(missing_ok=True)

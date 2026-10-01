"""Application data directory layout (DATABASE §6, DEPLOYMENT §12)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import platformdirs

from handoff.errors import HandOffError

_STORAGE_ROOTS = ("files", "received")


def default_data_dir() -> Path:
    """OS-standard app-data location: %APPDATA%/HandOff on Windows, ~/.local/share/HandOff."""
    return Path(platformdirs.user_data_dir("HandOff", appauthor=False, roaming=True))


@dataclass(frozen=True)
class AppPaths:
    root: Path

    @property
    def db_path(self) -> Path:
        return self.root / "handoff.db"

    @property
    def files_dir(self) -> Path:
        return self.root / "files"

    @property
    def received_dir(self) -> Path:
        return self.root / "received"

    @property
    def transfers_dir(self) -> Path:
        return self.root / "transfers"

    @property
    def temp_dir(self) -> Path:
        return self.root / "temp"

    @property
    def keys_dir(self) -> Path:
        return self.root / "keys"

    def ensure(self) -> None:
        for d in (self.root, self.files_dir, self.received_dir, self.transfers_dir, self.temp_dir):
            d.mkdir(parents=True, exist_ok=True)
        self.keys_dir.mkdir(parents=True, exist_ok=True)
        if os.name == "posix":
            self.keys_dir.chmod(0o700)

    def relative(self, path: Path) -> str:
        """Storage path as persisted in the database: relative, forward slashes."""
        return path.resolve().relative_to(self.root.resolve()).as_posix()

    def resolve_storage(self, rel: str) -> Path:
        """Resolve a stored relative path, refusing anything outside HandOff storage."""
        p = Path(rel)
        if p.is_absolute() or ".." in p.parts or not p.parts or p.parts[0] not in _STORAGE_ROOTS:
            raise HandOffError("INVALID_PATH", "Storage path is outside HandOff storage.")
        root = self.root.resolve()
        resolved = (root / p).resolve()
        if not resolved.is_relative_to(root / p.parts[0]):
            raise HandOffError("INVALID_PATH", "Storage path is outside HandOff storage.")
        return resolved

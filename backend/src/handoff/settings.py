"""User-facing settings service (ADR-038). Backed by the `settings` table."""

from __future__ import annotations

import re
import socket
from pathlib import Path

from handoff.audit import AuditEvent, record_event
from handoff.config import DEFAULT_HISTORY_RETENTION_DAYS
from handoff.db.engine import Database
from handoff.db.repositories import SettingsRepository, SettingValue
from handoff.destination import default_receive_dir, validate_receive_dir
from handoff.errors import HandOffError

# Keys the UI may change. device_name is derived (DATABASE §12); schema_version is internal.
USER_EDITABLE = frozenset({"history_retention", "receive_directory"})
# `receive_mode` is obsolete since ADR-055: old databases keep the row, nothing reads it.
_HIDDEN = frozenset({"schema_version", "receive_mode"})


def derive_device_name(hostname: str | None = None) -> str:
    """Device name from the OS (Phase 1: not user-editable)."""
    raw = hostname if hostname is not None else socket.gethostname()
    cleaned = re.sub(r"[^\w .-]", "", raw, flags=re.UNICODE).strip()[:64]
    return cleaned or "HandOff-Device"


class SettingsService:
    def __init__(self, db: Database, protected_dirs: tuple[Path, ...] = ()) -> None:
        self.db = db
        # Folders a destination may never be inside (HandOff's own data directory).
        self.protected_dirs = protected_dirs

    def ensure_defaults(self, hostname: str | None = None) -> None:
        """Create missing settings only; existing values are kept."""
        with self.db.session() as s:
            repo = SettingsRepository(s)
            if repo.get("history_retention") is None:
                repo.set("history_retention", DEFAULT_HISTORY_RETENTION_DAYS)
            if repo.get("device_name") is None:
                repo.set("device_name", derive_device_name(hostname))

    def get_all(self) -> dict[str, SettingValue]:
        with self.db.session() as s:
            values = {k: v for k, v in SettingsRepository(s).all().items() if k not in _HIDDEN}
        values["receive_directory"] = str(self.receive_directory(validate=False))
        return values

    def get(self, key: str) -> SettingValue | None:
        with self.db.session() as s:
            return SettingsRepository(s).get(key)

    def receive_directory(self, *, validate: bool = True) -> Path:
        """Where incoming files are written: the saved folder, else the OS Desktop (ADR-055).

        With `validate`, the folder is re-checked now (it may have been deleted or become
        read-only since it was chosen) and INVALID_PATH is raised if it is unusable.
        """
        saved = self.get("receive_directory")
        path = Path(saved) if isinstance(saved, str) and saved else default_receive_dir()
        if validate:
            return validate_receive_dir(str(path), forbidden=self.protected_dirs)
        return path

    def set_receive_directory(self, raw: object) -> Path:
        path = validate_receive_dir(raw, forbidden=self.protected_dirs)
        with self.db.session() as s:
            repo = SettingsRepository(s)
            old = repo.get("receive_directory")
            repo.set("receive_directory", str(path))
            if old != str(path):
                record_event(
                    s,
                    AuditEvent.RECEIVE_DIRECTORY_CHANGED,
                    "Receive folder changed.",
                    metadata={"old_value": old, "new_value": str(path)},
                )
        return path

    def set(self, key: str, value: SettingValue) -> None:
        if key not in USER_EDITABLE:
            raise HandOffError("INVALID_REQUEST", f"Setting '{key}' cannot be changed.")
        if key == "receive_directory":
            self.set_receive_directory(value)
            return
        if key == "history_retention" and (
            isinstance(value, bool) or not isinstance(value, int) or value < 0
        ):
            raise HandOffError(
                "INVALID_REQUEST", "history_retention must be a number of days (0 = forever)."
            )
        with self.db.session() as s:
            SettingsRepository(s).set(key, value)

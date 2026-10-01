"""User-facing settings service (ADR-038). Backed by the `settings` table."""

from __future__ import annotations

import re
import socket

from handoff.audit import AuditEvent, record_event
from handoff.config import DEFAULT_HISTORY_RETENTION_DAYS
from handoff.db.engine import Database
from handoff.db.repositories import SettingsRepository, SettingValue
from handoff.errors import HandOffError

# Keys the UI may change. device_name is derived (DATABASE §12); schema_version is internal.
USER_EDITABLE = frozenset({"history_retention"})
_HIDDEN = frozenset({"schema_version"})


def derive_device_name(hostname: str | None = None) -> str:
    """Device name from the OS (Phase 1: not user-editable)."""
    raw = hostname if hostname is not None else socket.gethostname()
    cleaned = re.sub(r"[^\w .-]", "", raw, flags=re.UNICODE).strip()[:64]
    return cleaned or "HandOff-Device"


class SettingsService:
    def __init__(self, db: Database) -> None:
        self.db = db

    def ensure_defaults(self, hostname: str | None = None) -> None:
        """Create missing settings only; existing values (e.g. Receive Mode) are kept."""
        with self.db.session() as s:
            repo = SettingsRepository(s)
            if repo.get("receive_mode") is None:
                repo.set("receive_mode", False)  # safest default: not accepting files
            if repo.get("history_retention") is None:
                repo.set("history_retention", DEFAULT_HISTORY_RETENTION_DAYS)
            if repo.get("device_name") is None:
                repo.set("device_name", derive_device_name(hostname))

    def get_all(self) -> dict[str, SettingValue]:
        with self.db.session() as s:
            return {k: v for k, v in SettingsRepository(s).all().items() if k not in _HIDDEN}

    def get(self, key: str) -> SettingValue | None:
        with self.db.session() as s:
            return SettingsRepository(s).get(key)

    def get_receive_mode(self) -> bool:
        return bool(self.get("receive_mode"))

    def set_receive_mode(self, enabled: bool) -> bool:
        if not isinstance(enabled, bool):
            raise HandOffError("INVALID_REQUEST", "'enabled' must be true or false.")
        with self.db.session() as s:
            repo = SettingsRepository(s)
            old = bool(repo.get("receive_mode"))
            repo.set("receive_mode", enabled)
            if old != enabled:
                record_event(
                    s,
                    AuditEvent.RECEIVE_MODE_CHANGED,
                    f"Receive Mode turned {'ON' if enabled else 'OFF'}.",
                    metadata={"old_value": old, "new_value": enabled},
                )
        return enabled

    def set(self, key: str, value: SettingValue) -> None:
        if key not in USER_EDITABLE:
            raise HandOffError("INVALID_REQUEST", f"Setting '{key}' cannot be changed.")
        if key == "history_retention" and (
            isinstance(value, bool) or not isinstance(value, int) or value < 0
        ):
            raise HandOffError(
                "INVALID_REQUEST", "history_retention must be a number of days (0 = forever)."
            )
        with self.db.session() as s:
            SettingsRepository(s).set(key, value)

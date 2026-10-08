"""Audit logging (DATABASE §27-30, SECURITY §41-43).

record_event() adds a row to the caller's session so the audit entry commits atomically
with the state change it describes. Never pass secrets in `message` or `metadata`.
"""

from __future__ import annotations

import json
from enum import StrEnum
from typing import Any

from sqlalchemy.orm import Session

from handoff.db.models import AuditLog, utcnow


class AuditEvent(StrEnum):
    DEVICE_DISCOVERED = "DEVICE_DISCOVERED"
    DEVICE_CONNECTED = "DEVICE_CONNECTED"
    DEVICE_OFFLINE = "DEVICE_OFFLINE"
    FILE_IMPORTED = "FILE_IMPORTED"
    FILE_DELETED = "FILE_DELETED"
    FILE_RECEIVED = "FILE_RECEIVED"
    TRANSFER_CREATED = "TRANSFER_CREATED"
    TRANSFER_STARTED = "TRANSFER_STARTED"
    TRANSFER_COMPLETED = "TRANSFER_COMPLETED"
    TRANSFER_PARTIALLY_COMPLETED = "TRANSFER_PARTIALLY_COMPLETED"
    TRANSFER_FAILED = "TRANSFER_FAILED"
    TRANSFER_REJECTED = "TRANSFER_REJECTED"
    RECEIVE_MODE_CHANGED = "RECEIVE_MODE_CHANGED"  # obsolete (ADR-055); kept for old history
    RECEIVE_DIRECTORY_CHANGED = "RECEIVE_DIRECTORY_CHANGED"
    HAND_CONTROL_ENABLED = "HAND_CONTROL_ENABLED"
    HAND_CONTROL_DISABLED = "HAND_CONTROL_DISABLED"
    APPLICATION_STARTED = "APPLICATION_STARTED"
    APPLICATION_STOPPED = "APPLICATION_STOPPED"
    INVALID_DEVICE = "INVALID_DEVICE"
    TLS_FAILURE = "TLS_FAILURE"
    UNSUPPORTED_FILE = "UNSUPPORTED_FILE"
    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    INVALID_PATH = "INVALID_PATH"
    INVALID_FILENAME = "INVALID_FILENAME"
    INVALID_HASH = "INVALID_HASH"


def record_event(
    session: Session,
    event: AuditEvent,
    message: str,
    *,
    actor: str = "local",
    device_id: str | None = None,
    file_id: str | None = None,
    transfer_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> AuditLog:
    row = AuditLog(
        event_type=event.value,
        actor=actor,
        device_id=device_id,
        file_id=file_id,
        transfer_id=transfer_id,
        message=message,
        meta=json.dumps(metadata, sort_keys=True) if metadata else None,
        created_at=utcnow(),
    )
    session.add(row)
    return row


# Validation failures (API code) -> the specific audit event (SECURITY §41).
REJECTION_EVENTS: dict[str, AuditEvent] = {
    "FILE_TYPE_NOT_SUPPORTED": AuditEvent.UNSUPPORTED_FILE,
    "FILE_TOO_LARGE": AuditEvent.FILE_TOO_LARGE,
    "INVALID_PATH": AuditEvent.INVALID_PATH,
    "INVALID_FILE": AuditEvent.INVALID_FILENAME,
}

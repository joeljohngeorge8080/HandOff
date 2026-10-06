"""Transfer state machine (API §14, DATABASE §22). There is no CANCELLED state (FR-026)."""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum

from handoff.errors import HandOffError


class TransferStatus(StrEnum):
    CREATED = "created"
    VALIDATING = "validating"
    ACCEPTED = "accepted"
    TRANSFERRING = "transferring"
    COMPLETED = "completed"
    FAILED = "failed"
    PARTIALLY_COMPLETED = "partially_completed"


class FileStatus(StrEnum):
    PENDING = "pending"
    TRANSFERRING = "transferring"
    COMPLETED = "completed"
    FAILED = "failed"


S = TransferStatus

TERMINAL_STATUSES: frozenset[TransferStatus] = frozenset(
    {S.COMPLETED, S.FAILED, S.PARTIALLY_COMPLETED}
)

_TRANSITIONS: dict[TransferStatus, frozenset[TransferStatus]] = {
    S.CREATED: frozenset({S.VALIDATING, S.FAILED}),
    S.VALIDATING: frozenset({S.ACCEPTED, S.FAILED}),
    S.ACCEPTED: frozenset({S.TRANSFERRING, S.FAILED}),
    S.TRANSFERRING: frozenset({S.COMPLETED, S.FAILED, S.PARTIALLY_COMPLETED}),
    S.COMPLETED: frozenset(),
    S.FAILED: frozenset(),
    S.PARTIALLY_COMPLETED: frozenset(),
}


def can_transition(src: TransferStatus, dst: TransferStatus) -> bool:
    return dst in _TRANSITIONS[src]


def assert_transition(src: TransferStatus, dst: TransferStatus) -> None:
    if not can_transition(src, dst):
        raise HandOffError(
            "INVALID_STATE",
            f"Transfer cannot change from {src.value} to {dst.value}.",
            {"from": src.value, "to": dst.value},
        )


def derive_job_status(files: Iterable[FileStatus]) -> TransferStatus:
    """Overall result of a finished job from its per-file results (NFR-009)."""
    statuses = list(files)
    if not statuses or any(f in (FileStatus.PENDING, FileStatus.TRANSFERRING) for f in statuses):
        raise HandOffError("INVALID_STATE", "Every file must be finished to derive the result.")
    if all(f == FileStatus.COMPLETED for f in statuses):
        return S.COMPLETED
    if all(f == FileStatus.FAILED for f in statuses):
        return S.FAILED
    return S.PARTIALLY_COMPLETED

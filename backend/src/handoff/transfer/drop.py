"""Edge drop pipeline (ADR-054): files dropped on the right edge become one transfer job.

`inspect` is a cheap, read-only check used while a drag is still in flight. `send` runs when the
user releases: it validates *every* item (all-or-nothing), imports each file into managed storage
(copy + SHA-256, ADR-049), and hands the file IDs to the existing `SenderService` (ADR-050). It
adds no protocol of its own.

The managed copies exist only to feed the transfer. With no gallery to manage them, they are
logically deleted once the transfer is terminal (success or failure); history and audit rows
stay. Copies orphaned by a crash are swept at startup (`sweep_orphans`).
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from handoff.config import MAX_FILES_PER_TRANSFER
from handoff.core import Core
from handoff.db.repositories import FileRepository, TransferRepository
from handoff.devices.connection import ConnectionManager
from handoff.errors import HandOffError
from handoff.transfer.sender import SenderService

log = logging.getLogger(__name__)

NO_PEER_MESSAGE = "No HandOff device connected"

_REASONS = {
    "FILE_TYPE_NOT_SUPPORTED": "unsupported_type",
    "FILE_TOO_LARGE": "too_large",
    "INVALID_PATH": "invalid_name",
    "INVALID_FILE": "invalid_name",
    "FILE_NOT_FOUND": "missing",
    "FILE_STORAGE_ERROR": "unreadable",
}


def _reason(exc: HandOffError) -> str:
    detail = exc.details.get("reason")
    return detail if isinstance(detail, str) else _REASONS.get(exc.code, "invalid")


class DropService:
    def __init__(self, core: Core, connections: ConnectionManager, sender: SenderService) -> None:
        self.core, self.connections, self.sender = core, connections, sender
        self._lock = threading.RLock()
        self._ephemeral: dict[str, list[str]] = {}  # transfer id -> managed file ids
        sender.on_finished.append(self._cleanup)

    # ----- helpers -----------------------------------------------------------------------

    @staticmethod
    def _paths(raw: object) -> list[str]:
        if not isinstance(raw, list) or not raw:
            raise HandOffError("INVALID_REQUEST", "'paths' must be a non-empty list of paths.")
        if len(raw) > MAX_FILES_PER_TRANSFER:
            raise HandOffError(
                "INVALID_REQUEST", f"At most {MAX_FILES_PER_TRANSFER} files per transfer."
            )
        for p in raw:
            if not isinstance(p, str) or not p or "\x00" in p:
                raise HandOffError("INVALID_REQUEST", "Every path must be a non-empty string.")
        return list(raw)

    def _check_items(self, paths: list[str], *, audit: bool) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for path in paths:
            name = path.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1] or path
            try:
                name, ext, size = self.core.files.validate_source(path)
                items.append({"name": name, "ok": True, "size": size, "extension": ext})
            except HandOffError as exc:
                if audit:
                    self.core.files.audit_rejection(exc, name)
                items.append(
                    {
                        "name": name[:120], "ok": False, "code": exc.code,
                        "reason": _reason(exc), "message": exc.message,
                    }
                )  # fmt: skip
        return items

    # ----- drop.inspect ------------------------------------------------------------------

    def inspect(self, raw_paths: object) -> dict[str, Any]:
        """Read-only: would this drop be accepted? Copies nothing and records nothing."""
        items = self._check_items(self._paths(raw_paths), audit=False)
        good = [i for i in items if i["ok"]]
        return {
            "ok": len(good) == len(items),
            "file_count": len(items),
            "total_size": sum(i["size"] for i in good),
            "items": items,
        }

    # ----- drop.send ---------------------------------------------------------------------

    def send(self, raw_paths: object) -> dict[str, Any]:
        paths = self._paths(raw_paths)
        peer = self.connections.active_peer()
        if peer is None:
            raise HandOffError("DEVICE_NOT_FOUND", NO_PEER_MESSAGE)
        if peer.status != "connected":
            raise HandOffError("DEVICE_OFFLINE", f"{peer.device_name} is offline.")
        with self.core.db.session() as s:
            if TransferRepository(s).get_active() is not None:
                raise HandOffError("INVALID_STATE", "A transfer is already in progress.")

        items = self._check_items(paths, audit=True)
        bad = next((i for i in items if not i["ok"]), None)
        if bad is not None:  # all-or-nothing: never silently send a subset
            raise HandOffError(bad["code"], bad["message"], {"items": items})

        imported: list[str] = []
        try:
            for path in paths:
                imported.append(self.core.files.import_file(path)["id"])
            with self._lock:  # a fast failure must not clean up before we registered the copies
                transfer = self.sender.create(imported, peer.device_id)
                self._ephemeral[transfer["transfer_id"]] = imported
        except BaseException:
            self._delete(imported)
            raise
        return {"transfer": transfer}

    # ----- cleanup -----------------------------------------------------------------------

    def _cleanup(self, transfer_id: str) -> None:
        with self._lock:
            ids = self._ephemeral.pop(transfer_id, [])
        self._delete(ids)

    def _delete(self, file_ids: list[str]) -> None:
        for fid in file_ids:
            try:
                self.core.files.delete_file(fid)
            except HandOffError as exc:
                if exc.code != "FILE_NOT_FOUND":
                    log.error("Could not remove dropped copy %s: %s", fid, exc.message)

    def sweep_orphans(self) -> int:
        """Delete managed copies no transfer will ever use (left behind by a crash)."""
        with self.core.db.session() as s:
            ids = [f.id for f in FileRepository(s).list_active() if f.source == "imported"]
        self._delete(ids)
        return len(ids)

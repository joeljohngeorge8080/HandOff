"""Transfer history: listing and retention cleanup (FR-031, FR-032, DATABASE §34-36)."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from handoff.db.engine import Database
from handoff.db.models import Transfer, iso, utcnow
from handoff.db.repositories import DeviceRepository, SettingsRepository, TransferRepository

log = logging.getLogger(__name__)


def serialize_transfer(t: Transfer, peer_names: dict[str, str]) -> dict[str, Any]:
    peer_id = t.destination_device_id if t.direction == "sent" else t.source_device_id
    return {
        "transfer_id": t.id,
        "direction": t.direction,
        "peer_device_id": peer_id,
        "peer_device_name": peer_names.get(peer_id) if peer_id else None,
        "file_count": t.file_count,
        "total_size": t.total_size_bytes,
        "archive_size": t.archive_size_bytes,
        "bytes_transferred": t.bytes_transferred,
        "status": t.status,
        "error_code": t.error_code,
        "error_message": t.error_message,
        "created_at": iso(t.created_at),
        "completed_at": iso(t.completed_at),
        "files": [
            {
                "name": f.original_name,
                "size": f.size_bytes,
                "status": f.status,
                "failure_code": f.failure_code,
            }
            for f in t.files
        ],
    }


class HistoryService:
    def __init__(self, db: Database) -> None:
        self.db = db

    def list(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        with self.db.session() as s:
            transfers = TransferRepository(s).list_history(limit, offset)
            devices = {d.device_id: d.device_name for d in DeviceRepository(s).list_all()}
            return [serialize_transfer(t, devices) for t in transfers]

    def get(self, transfer_id: str) -> dict[str, Any] | None:
        with self.db.session() as s:
            t = TransferRepository(s).get(transfer_id)
            if t is None:
                return None
            devices = {d.device_id: d.device_name for d in DeviceRepository(s).list_all()}
            return serialize_transfer(t, devices)

    def active(self) -> dict[str, Any] | None:
        with self.db.session() as s:
            t = TransferRepository(s).get_active()
            if t is None:
                return None
            devices = {d.device_id: d.device_name for d in DeviceRepository(s).list_all()}
            return serialize_transfer(t, devices)

    def cleanup(self, now: datetime | None = None) -> int:
        """Delete finished transfers older than the retention setting. 0 days = keep forever.

        Never deletes audit_logs or active transfers.
        """
        now = now or utcnow()
        with self.db.session() as s:
            days = SettingsRepository(s).get("history_retention", 0)
            if not isinstance(days, int) or isinstance(days, bool) or days <= 0:
                return 0
            removed = TransferRepository(s).delete_terminal_before(now - timedelta(days=days))
        if removed:
            log.info("History cleanup removed %d old transfer(s)", removed)
        return removed


class PeriodicTask:
    """Runs `fn` now and then every `interval` seconds on a daemon thread."""

    def __init__(self, fn: Callable[[], object], interval: float, name: str) -> None:
        self._fn, self._interval = fn, interval
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._fn()
            except Exception:
                log.exception("Periodic task %s failed", self._thread.name)
            self._stop.wait(self._interval)

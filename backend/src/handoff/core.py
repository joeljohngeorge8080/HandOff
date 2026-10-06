"""Application core: wires services together and owns startup/shutdown (ARCHTECTURE §6)."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from handoff.audit import AuditEvent, record_event
from handoff.db.engine import Database, open_database
from handoff.db.repositories import TransferRepository
from handoff.db.schema import init_schema
from handoff.events import EventBus
from handoff.files.manager import FileManager
from handoff.history import HistoryService
from handoff.identity import Identity, load_or_create_identity
from handoff.paths import AppPaths
from handoff.settings import SettingsService

log = logging.getLogger(__name__)


class Core:
    def __init__(self, paths: AppPaths, *, hostname: str | None = None) -> None:
        self.paths = paths
        self._hostname = hostname
        self.db: Database
        self.identity: Identity
        self.settings: SettingsService
        self.files: FileManager
        self.history: HistoryService
        self.events = EventBus()

    def start(self) -> None:
        self.paths.ensure()
        self.db = open_database(self.paths)
        try:
            self._start_services()
        except BaseException:
            self.db.dispose()  # never leave the database open after a failed start
            raise

    def _start_services(self) -> None:
        init_schema(self.db)
        self.identity = load_or_create_identity(self.paths)
        self.settings = SettingsService(self.db, protected_dirs=(self.paths.root,))
        self.settings.ensure_defaults(self._hostname)
        self.files = FileManager(self.paths, self.db)
        self.history = HistoryService(self.db)
        self.events.history = self.history
        self._recover_interrupted_transfers()
        self._clear_scratch_dirs()
        with self.db.session() as s:
            record_event(s, AuditEvent.APPLICATION_STARTED, "HandOff started.")

    def close(self) -> None:
        with self.db.session() as s:
            record_event(s, AuditEvent.APPLICATION_STOPPED, "HandOff stopped.")
        self.db.dispose()

    @property
    def device_name(self) -> str:
        return str(self.settings.get("device_name"))

    def _recover_interrupted_transfers(self) -> None:
        """Anything left unfinished by a crash can never be reported as successful."""
        with self.db.session() as s:
            failed = TransferRepository(s).fail_all_non_terminal(
                "TRANSFER_INCOMPLETE", "HandOff closed before this transfer finished."
            )
            for transfer_id in failed:
                record_event(
                    s,
                    AuditEvent.TRANSFER_FAILED,
                    "Transfer interrupted by application shutdown.",
                    transfer_id=transfer_id,
                )

    def _clear_scratch_dirs(self) -> None:
        """No transfer is running at startup, so temp/ and transfers/ hold only leftovers."""
        for base in (self.paths.temp_dir, self.paths.transfers_dir):
            for child in base.iterdir():
                try:
                    if child.is_dir() and not child.is_symlink():
                        shutil.rmtree(child)
                    else:
                        Path(child).unlink()
                except OSError as exc:
                    log.warning("Could not remove leftover %s: %s", child, exc)

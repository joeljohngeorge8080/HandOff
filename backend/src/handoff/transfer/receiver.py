"""Receiving side of a transfer (API §15-24, SECURITY §19-40).

Everything from the network is untrusted. A transfer is only marked complete after the
archive was fully received, structurally validated, every file extracted within its declared
size, verified against the manifest's SHA-256, moved into final storage, and the database
updated. Any other outcome is `failed` or `partially_completed`, never success.
"""

from __future__ import annotations

import logging
import re
import shutil
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.exc import IntegrityError

from handoff.audit import REJECTION_EVENTS, AuditEvent, record_event
from handoff.config import ACCEPT_TIMEOUT_SECONDS, MAX_AUTO_OPEN_FILES
from handoff.core import Core
from handoff.db.models import Transfer, TransferFile, utcnow
from handoff.db.repositories import TransferRepository
from handoff.destination import copy_verified
from handoff.devices.connection import ConnectionManager
from handoff.errors import HandOffError, must
from handoff.peer_api.auth import PeerIdentity
from handoff.peer_api.signing import FailureLimiter
from handoff.transfer.archive import ExtractedFile, safe_extract
from handoff.transfer.manifest import Manifest
from handoff.transfer.states import TERMINAL_STATUSES, FileStatus, TransferStatus, derive_job_status

log = logging.getLogger(__name__)

_ARCHIVE_NAME = re.compile(r"^[A-Za-z0-9._-]{1,100}\.zip$")
_ARCHIVE_OVERHEAD_PER_FILE = 1024
_ARCHIVE_OVERHEAD_BASE = 4096
_DISK_SAFETY_MARGIN = 1024 * 1024


@dataclass
class ReceiverJob:
    manifest: Manifest
    source_device_id: str
    idempotency_key: str | None
    created_at: float
    work_dir: Path
    payload_path: Path
    max_bytes: int
    state: str = "accepted"  # accepted -> uploading -> done
    bytes_received: int = 0


class ReceiverService:
    def __init__(
        self,
        core: Core,
        connections: ConnectionManager,
        limiter: FailureLimiter,
        *,
        clock: Callable[[], float] = time.monotonic,
        accept_timeout: float = ACCEPT_TIMEOUT_SECONDS,
    ) -> None:
        self.core, self.connections, self.limiter = core, connections, limiter
        self.clock, self.accept_timeout = clock, accept_timeout
        self._jobs: dict[str, ReceiverJob] = {}
        self._lock = threading.RLock()

    # ----- helpers -----------------------------------------------------------------------

    def _free_bytes(self, where: Path | None = None) -> int:
        return shutil.disk_usage(where or self.core.paths.transfers_dir).free

    def _destination(self) -> Path:
        """The receiver's own validated destination folder (ADR-055). Never network input."""
        try:
            return self.core.settings.receive_directory()
        except HandOffError as exc:
            raise HandOffError(
                "RECEIVER_NOT_READY",
                "This device's receive folder is not available. Choose another folder.",
                {"reason": exc.message},
            ) from exc

    def _audit_rejection(
        self,
        sender: PeerIdentity,
        exc: HandOffError,
        transfer_id: str | None,
    ) -> None:
        with self.core.db.session() as s:
            specific = REJECTION_EVENTS.get(exc.code)
            if specific:
                record_event(
                    s, specific, f"Transfer rejected: {exc.message}",
                    device_id=sender.device_id, transfer_id=transfer_id,
                    metadata={"code": exc.code},
                )  # fmt: skip
            record_event(
                s, AuditEvent.TRANSFER_REJECTED, f"Transfer rejected: {exc.message}",
                device_id=sender.device_id, transfer_id=transfer_id, metadata={"code": exc.code},
            )  # fmt: skip

    def _reject(
        self, sender: PeerIdentity, exc: HandOffError, transfer_id: str | None, *, count: bool
    ) -> HandOffError:
        if count:
            self.limiter.record(sender.device_id)
        self._audit_rejection(sender, exc, transfer_id)
        return exc

    @staticmethod
    def _created_response(job: ReceiverJob) -> dict[str, Any]:
        tid = job.manifest.transfer_id
        return {
            "transfer_id": tid,
            "status": "accepted",
            "upload_url": f"/api/v1/transfers/{tid}/data",
        }

    # ----- POST /transfers ---------------------------------------------------------------

    def create(
        self, sender: PeerIdentity, body: object, idempotency_key: str | None
    ) -> tuple[dict[str, Any], bool]:
        """Returns (response, created). created is False for an idempotent retry."""
        self.expire_stale()
        if self.limiter.blocked(sender.device_id):
            raise HandOffError("RATE_LIMITED", "Too many invalid requests. Try again later.")
        if not isinstance(body, dict):
            raise HandOffError("INVALID_REQUEST", "Request body must be a JSON object.")
        if body.get("source_device_id") != sender.device_id:
            raise self._reject(
                sender,
                HandOffError("INVALID_DEVICE_ID", "source_device_id does not match the sender."),
                None,
                count=True,
            )
        self.connections.allow_inbound_transfer(sender.device_id)

        raw_id = body.get("transfer_id")
        tid_for_audit = raw_id[:64] if isinstance(raw_id, str) else None
        try:
            destination = self._destination()
        except HandOffError as exc:
            raise self._reject(sender, exc, tid_for_audit, count=False) from None
        try:
            manifest = Manifest.from_dict({"transfer_id": raw_id, "files": body.get("files")})
            self._check_declared(body, manifest)
        except HandOffError as exc:
            raise self._reject(sender, exc, tid_for_audit, count=True) from None

        tid = manifest.transfer_id
        key = idempotency_key[:128] if idempotency_key else None
        with self._lock:
            existing = self._jobs.get(tid)
            if existing is not None:
                same = (
                    key is not None
                    and existing.idempotency_key == key
                    and existing.source_device_id == sender.device_id
                    and existing.state == "accepted"
                )
                if same:
                    return self._created_response(existing), False
                raise self._reject(
                    sender,
                    HandOffError("TRANSFER_ALREADY_EXISTS", "This transfer ID was already used."),
                    tid,
                    count=True,
                )
            self._check_not_replayed(sender, tid)
            self._check_disk(sender, manifest, destination)
            self._insert_transfer(sender, manifest)
            work = self.core.paths.transfers_dir / tid
            work.mkdir(parents=True, exist_ok=True)
            cap = (
                manifest.total_size
                + len(manifest.files) * _ARCHIVE_OVERHEAD_PER_FILE
                + _ARCHIVE_OVERHEAD_BASE
            )
            job = ReceiverJob(
                manifest, sender.device_id, key, self.clock(), work, work / "payload.zip", cap
            )
            self._jobs[tid] = job
        self.core.events.transfer_changed(tid)
        return self._created_response(job), True

    @staticmethod
    def _check_declared(body: dict[str, Any], manifest: Manifest) -> None:
        if (
            body.get("file_count") != len(manifest.files)
            or body.get("total_size") != manifest.total_size
        ):
            raise HandOffError(
                "TRANSFER_VALIDATION_FAILED",
                "Declared file count or size does not match the manifest.",
            )
        name = body.get("archive_name")
        if name is not None and not (isinstance(name, str) and _ARCHIVE_NAME.match(name)):
            raise HandOffError("TRANSFER_VALIDATION_FAILED", "Invalid archive name.")

    def _check_not_replayed(self, sender: PeerIdentity, tid: str) -> None:
        with self.core.db.session() as s:
            known = TransferRepository(s).get(tid) is not None
        if known:  # SECURITY §37: a used transfer ID is never accepted again
            raise self._reject(
                sender,
                HandOffError("TRANSFER_ALREADY_EXISTS", "This transfer ID was already used."),
                tid,
                count=True,
            )

    def _check_disk(self, sender: PeerIdentity, manifest: Manifest, destination: Path) -> None:
        staging_needed = 2 * manifest.total_size + _DISK_SAFETY_MARGIN  # archive + extracted copy
        dest_needed = manifest.total_size + _DISK_SAFETY_MARGIN
        needed = staging_needed
        short = self._free_bytes() < staging_needed
        if not short and self._free_bytes(destination) < dest_needed:
            short, needed = True, dest_needed
        if short:
            raise self._reject(
                sender,
                HandOffError(
                    "INSUFFICIENT_STORAGE", "This device does not have enough free storage.",
                    {"required_bytes": needed},
                ),
                manifest.transfer_id,
                count=False,
            )  # fmt: skip

    def _insert_transfer(self, sender: PeerIdentity, manifest: Manifest) -> None:
        now = utcnow()
        tid = manifest.transfer_id
        try:
            with self.core.db.session() as s:
                repo = TransferRepository(s)
                t = Transfer(
                    id=tid, direction="received", source_device_id=sender.device_id,
                    destination_device_id=None, status=TransferStatus.CREATED.value,
                    file_count=len(manifest.files), total_size_bytes=manifest.total_size,
                    created_at=now,
                )  # fmt: skip
                rows = [
                    TransferFile(
                        file_id=None, original_name=f.filename, size_bytes=f.size,
                        sha256=f.sha256, status=FileStatus.PENDING.value, created_at=now,
                    )
                    for f in manifest.files
                ]  # fmt: skip
                repo.add(t, rows)
                repo.set_status(t, TransferStatus.VALIDATING)
                repo.set_status(t, TransferStatus.ACCEPTED)
                record_event(
                    s, AuditEvent.TRANSFER_CREATED, "Incoming transfer accepted.",
                    device_id=sender.device_id, transfer_id=tid,
                    metadata={"file_count": len(rows), "total_size": manifest.total_size},
                )  # fmt: skip
        except HandOffError as exc:  # another transfer is active
            raise self._reject(sender, exc, tid, count=False) from None
        except IntegrityError as exc:
            if "transfers.id" not in str(exc.orig):
                raise  # some other constraint: a real bug, not a replay
            raise self._reject(
                sender,
                HandOffError("TRANSFER_ALREADY_EXISTS", "This transfer ID was already used."),
                tid,
                count=True,
            ) from exc

    # ----- POST /transfers/{id}/data -----------------------------------------------------

    def begin_upload(self, sender: PeerIdentity, transfer_id: str) -> ReceiverJob:
        self.expire_stale()
        with self._lock:
            job = self._jobs.get(transfer_id)
            if job is None or job.source_device_id != sender.device_id:
                raise HandOffError("TRANSFER_NOT_FOUND", "Transfer not found.")
            if job.state != "accepted":
                raise HandOffError("INVALID_STATE", "This transfer is not waiting for data.")
            job.state = "uploading"
        with self.core.db.session() as s:
            repo = TransferRepository(s)
            t = must(repo.get(transfer_id), "transfer")
            repo.set_status(t, TransferStatus.TRANSFERRING)
            for tf in t.files:
                tf.status = FileStatus.TRANSFERRING.value
            record_event(
                s, AuditEvent.TRANSFER_STARTED, "Receiving files.",
                device_id=sender.device_id, transfer_id=transfer_id,
            )  # fmt: skip
        self.core.events.transfer_changed(transfer_id)
        return job

    def process_upload(self, job: ReceiverJob) -> dict[str, Any]:
        try:
            try:
                results = safe_extract(job.payload_path, job.work_dir / "out", job.manifest)
            except HandOffError as exc:
                self._audit_archive_rejection(job, exc)
                self.fail(job, exc.code, exc.message)
                raise
            return self._commit(job, results)
        except HandOffError:
            raise
        except Exception as exc:
            log.exception("Transfer %s failed unexpectedly", job.manifest.transfer_id)
            self.fail(job, "INTERNAL_ERROR", "The transfer could not be completed.")
            raise HandOffError("INTERNAL_ERROR", "The transfer could not be completed.") from exc
        finally:
            self._cleanup(job)
            self.core.events.transfer_changed(job.manifest.transfer_id)

    def _audit_archive_rejection(self, job: ReceiverJob, exc: HandOffError) -> None:
        with self.core.db.session() as s:
            specific = REJECTION_EVENTS.get(exc.code)
            if specific:
                record_event(
                    s, specific, f"Archive rejected: {exc.message}",
                    device_id=job.source_device_id, transfer_id=job.manifest.transfer_id,
                    metadata={"code": exc.code},
                )  # fmt: skip

    def _commit(self, job: ReceiverJob, results: list[ExtractedFile]) -> dict[str, Any]:
        """Copy each verified file into the receiver's folder, then record the outcome.

        Per-file failures (bad hash on re-verification, unwritable folder) become failed
        files, so the job can end `partially_completed` or `failed`, never falsely complete.
        """
        created: list[Path] = []
        try:
            stored = self._copy_to_destination(job, results, created)
            with self.core.db.session() as s:
                reply = self._record_results(s, job, results, stored)
        except BaseException:
            for p in created:  # only files this transfer created; nothing pre-existing
                p.unlink(missing_ok=True)
            raise
        self._auto_open(job, [stored[i] for i in sorted(stored)])
        return reply

    def _auto_open(self, job: ReceiverJob, paths: list[Path]) -> None:
        """Open verified, stored files when the user turned that on (ADR-061).

        Runs only after the outcome is committed, so it can never change it: a viewer that
        is missing or fails is logged and audited, and the transfer stays as recorded.
        """
        if not paths or not self.core.settings.auto_open_received():
            return
        for path in paths[:MAX_AUTO_OPEN_FILES]:
            try:
                self.core.opener(path)
            except Exception as exc:  # any viewer problem; the transfer is already final
                code = exc.code if isinstance(exc, HandOffError) else "OPEN_FAILED"
                log.warning("Could not open received file %s (%s)", path.name, code, exc_info=True)
                with self.core.db.session() as s:
                    record_event(
                        s, AuditEvent.AUTO_OPEN_FAILED, f"Could not open {path.name}.",
                        device_id=job.source_device_id, transfer_id=job.manifest.transfer_id,
                        metadata={"file_name": path.name, "code": code},
                    )  # fmt: skip

    def _copy_to_destination(
        self, job: ReceiverJob, results: list[ExtractedFile], created: list[Path]
    ) -> dict[int, Path]:
        try:
            destination = self._destination()
        except HandOffError as exc:
            for r in results:
                if r.ok:
                    r.ok, r.failure_code, r.failure_message = False, exc.code, exc.message
            return {}
        stored: dict[int, Path] = {}
        for i, r in enumerate(results):
            if not (r.ok and r.path is not None):
                continue
            try:
                final = copy_verified(r.path, destination, r.manifest.filename, r.manifest.sha256)
            except HandOffError as exc:
                r.ok, r.failure_code, r.failure_message = False, exc.code, exc.message
                continue
            created.append(final)
            stored[i] = final
        return stored

    def _record_results(
        self,
        s: Any,
        job: ReceiverJob,
        results: list[ExtractedFile],
        stored: dict[int, Path],
    ) -> dict[str, Any]:
        tid = job.manifest.transfer_id
        repo = TransferRepository(s)
        t = must(repo.get(tid), "transfer")
        now = utcnow()
        for i, (tf, r) in enumerate(zip(t.files, results, strict=True)):
            tf.completed_at = now
            if r.ok:
                written = stored[i]
                # Received files are not managed storage (ADR-055): no `files` row. The
                # transfer keeps the sender's name (the contract key); the name actually
                # written, after any `(1)` suffix, is in the audit entry and the reply.
                tf.status = FileStatus.COMPLETED.value
                record_event(
                    s, AuditEvent.FILE_RECEIVED, f"Received {written.name}.",
                    device_id=job.source_device_id, transfer_id=tid,
                    metadata={"file_name": written.name, "size_bytes": r.manifest.size},
                )  # fmt: skip
            else:
                tf.status = FileStatus.FAILED.value
                tf.failure_code, tf.failure_message = r.failure_code, r.failure_message
                if r.failure_code == "INVALID_HASH":
                    record_event(
                        s, AuditEvent.INVALID_HASH, "SHA-256 verification failed.",
                        device_id=job.source_device_id, transfer_id=tid,
                        metadata={"file_name": r.manifest.filename},
                    )  # fmt: skip
                elif r.failure_code == "FILE_TYPE_NOT_SUPPORTED":
                    record_event(
                        s, AuditEvent.UNSUPPORTED_FILE, "Executable content rejected.",
                        device_id=job.source_device_id, transfer_id=tid,
                        metadata={"file_name": r.manifest.filename},
                    )  # fmt: skip
        final = derive_job_status([FileStatus(tf.status) for tf in t.files])
        failures = [tf for tf in t.files if tf.status == FileStatus.FAILED.value]
        repo.set_status(
            t, final,
            error_code=failures[0].failure_code if failures else None,
            error_message=failures[0].failure_message if failures else None,
        )  # fmt: skip
        t.bytes_transferred = job.bytes_received
        event = {
            TransferStatus.COMPLETED: AuditEvent.TRANSFER_COMPLETED,
            TransferStatus.PARTIALLY_COMPLETED: AuditEvent.TRANSFER_PARTIALLY_COMPLETED,
            TransferStatus.FAILED: AuditEvent.TRANSFER_FAILED,
        }[final]
        record_event(
            s, event, f"Transfer {final.value.replace('_', ' ')}.",
            device_id=job.source_device_id, transfer_id=tid,
            metadata={"failed_files": [tf.original_name for tf in failures]},
        )  # fmt: skip
        job.state = "done"
        return {
            "transfer_id": tid,
            "status": final.value,
            "files_received": len(t.files) - len(failures),
            "files": [
                {
                    "name": tf.original_name,
                    "status": tf.status,
                    "failure_code": tf.failure_code,
                    **({"saved_as": stored[i].name} if i in stored else {}),
                }
                for i, tf in enumerate(t.files)
            ],
        }

    # ----- failure / cleanup -------------------------------------------------------------

    def fail(self, job: ReceiverJob, code: str, message: str) -> None:
        """Mark the transfer failed (idempotent) and remove all temporary data."""
        tid = job.manifest.transfer_id
        with self.core.db.session() as s:
            repo = TransferRepository(s)
            t = repo.get(tid)
            if t is not None and TransferStatus(t.status) not in TERMINAL_STATUSES:
                repo.set_status(t, TransferStatus.FAILED, error_code=code, error_message=message)
                for tf in t.files:
                    if tf.status in (FileStatus.PENDING.value, FileStatus.TRANSFERRING.value):
                        tf.status, tf.failure_code, tf.failure_message = (
                            FileStatus.FAILED.value, code, message,
                        )  # fmt: skip
                record_event(
                    s, AuditEvent.TRANSFER_FAILED, f"Transfer failed: {message}",
                    device_id=job.source_device_id, transfer_id=tid, metadata={"code": code},
                )  # fmt: skip
        self._cleanup(job)
        self.core.events.transfer_changed(tid)

    def _cleanup(self, job: ReceiverJob) -> None:
        with self._lock:
            if self._jobs.get(job.manifest.transfer_id) is job:
                del self._jobs[job.manifest.transfer_id]
        try:
            shutil.rmtree(job.work_dir)
        except FileNotFoundError:
            pass
        except OSError as exc:
            log.warning("Could not remove transfer directory %s: %s", job.work_dir, exc)

    def expire_stale(self) -> None:
        """Fail accepted transfers that never received data (the sender vanished)."""
        now = self.clock()
        with self._lock:
            stale = [
                j for j in self._jobs.values()
                if j.state == "accepted" and now - j.created_at > self.accept_timeout
            ]  # fmt: skip
        for job in stale:
            self.fail(job, "REQUEST_TIMEOUT", "No data was received in time.")

    # ----- GET /transfers/{id} -----------------------------------------------------------

    def status(self, sender: PeerIdentity, transfer_id: str) -> dict[str, Any]:
        with self.core.db.session() as s:
            t = TransferRepository(s).get(transfer_id)
            if t is None or t.direction != "received" or t.source_device_id != sender.device_id:
                raise HandOffError("TRANSFER_NOT_FOUND", "Transfer not found.")
            with self._lock:
                job = self._jobs.get(transfer_id)
            received = job.bytes_received if job else t.bytes_transferred
            total = t.total_size_bytes
            return {
                "transfer_id": t.id,
                "status": t.status,
                "file_count": t.file_count,
                "files_completed": sum(1 for f in t.files if f.status == "completed"),
                "total_size": total,
                "bytes_received": received,
                "progress": round(min(1.0, received / total), 3) if total else 1.0,
            }

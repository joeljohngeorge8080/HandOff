"""Sending side of a transfer (API §15-24, FR-022..FR-027).

`create` validates and records the job, then the transfer runs on a background thread so the
UI never blocks (NFR-006). Whatever happens, the job ends in a terminal state: it is never
reported as completed unless the receiver confirmed it after verifying every file.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import httpx

from handoff.audit import AuditEvent, record_event
from handoff.config import (
    CONNECT_TIMEOUT_SECONDS,
    IO_CHUNK_SIZE,
    MAX_FILES_PER_TRANSFER,
    REQUEST_TIMEOUT_SECONDS,
    UPLOAD_RESPONSE_TIMEOUT_SECONDS,
)
from handoff.core import Core
from handoff.db.models import File, Transfer, TransferFile, utcnow
from handoff.db.repositories import FileRepository, TransferRepository
from handoff.devices.client import PeerClient, error_from_response
from handoff.devices.connection import ActivePeer, ConnectionManager
from handoff.errors import HandOffError, must
from handoff.transfer.archive import build_archive
from handoff.transfer.manifest import Manifest, build_manifest
from handoff.transfer.states import TERMINAL_STATUSES, FileStatus, TransferStatus, derive_job_status

log = logging.getLogger(__name__)

_PROGRESS_INTERVAL = 0.25


class SenderService:
    def __init__(self, core: Core, connections: ConnectionManager, client: PeerClient) -> None:
        self.core, self.connections, self.client = core, connections, client
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="transfer")
        self._abort = threading.Event()
        self._abort_reason: HandOffError | None = None

    def stop(self) -> None:
        self.abort_active("DEVICE_OFFLINE", "HandOff is shutting down.")
        self._executor.shutdown(wait=True, cancel_futures=True)

    def abort_active(self, code: str, message: str) -> None:
        """Called when the peer goes offline: stop the running upload promptly."""
        self._abort_reason = HandOffError(code, message)
        self._abort.set()

    # ----- transfer.create ---------------------------------------------------------------

    def create(self, file_ids: object, destination_device_id: object) -> dict[str, Any]:
        ids = self._validate_ids(file_ids)
        if not isinstance(destination_device_id, str) or not destination_device_id:
            raise HandOffError("INVALID_REQUEST", "'destination_device_id' must be a string.")
        peer = self.connections.require_connected(destination_device_id)
        with self.core.db.session() as s:
            if TransferRepository(s).get_active() is not None:
                raise HandOffError("INVALID_STATE", "Another transfer is already active.")
        self._require_receiver_ready(peer)

        tid = f"tr_{uuid.uuid4().hex}"
        entries, manifest = self._prepare(tid, ids)
        now = utcnow()
        with self.core.db.session() as s:
            repo = TransferRepository(s)
            t = Transfer(
                id=tid, direction="sent", source_device_id=None,
                destination_device_id=peer.device_id, status=TransferStatus.CREATED.value,
                file_count=len(manifest.files), total_size_bytes=manifest.total_size,
                created_at=now,
            )  # fmt: skip
            rows = [
                TransferFile(
                    file_id=f.file_id, original_name=f.filename, size_bytes=f.size,
                    sha256=f.sha256, status=FileStatus.PENDING.value, created_at=now,
                )
                for f in manifest.files
            ]  # fmt: skip
            repo.add(t, rows)
            record_event(
                s, AuditEvent.TRANSFER_CREATED, f"Transfer to {peer.device_name} created.",
                device_id=peer.device_id, transfer_id=tid,
                metadata={"file_count": len(rows), "total_size": manifest.total_size},
            )  # fmt: skip
        self._executor.submit(self._run, tid, manifest, entries, peer)
        return must(self.core.history.get(tid), "transfer")

    @staticmethod
    def _validate_ids(file_ids: object) -> list[str]:
        if (
            not isinstance(file_ids, list)
            or not file_ids
            or not all(isinstance(i, str) and i for i in file_ids)
        ):
            raise HandOffError("INVALID_REQUEST", "'file_ids' must be a non-empty list of IDs.")
        if len(set(file_ids)) != len(file_ids):
            raise HandOffError("INVALID_REQUEST", "'file_ids' must not contain duplicates.")
        if len(file_ids) > MAX_FILES_PER_TRANSFER:
            raise HandOffError(
                "INVALID_REQUEST", f"At most {MAX_FILES_PER_TRANSFER} files per transfer."
            )
        return list(file_ids)

    def _require_receiver_ready(self, peer: ActivePeer) -> None:
        """FR-023: find out before sending whether the other device is accepting files."""
        address, port = peer.endpoint()
        resp = self.client.request(
            address, port, "GET", "/api/v1/device", expected_key=peer.public_key
        )
        if resp.status_code != 200:
            raise error_from_response(resp)
        try:
            accepting = resp.json()["receive_mode"] is True
        except (ValueError, KeyError, TypeError) as exc:
            raise HandOffError("NETWORK_ERROR", "Unexpected response from the device.") from exc
        if not accepting:
            with self.core.db.session() as s:
                record_event(
                    s, AuditEvent.TRANSFER_REJECTED, f"{peer.device_name} is not accepting files.",
                    device_id=peer.device_id, metadata={"code": "RECEIVE_MODE_DISABLED"},
                )  # fmt: skip
            raise HandOffError(
                "RECEIVE_MODE_DISABLED", f"{peer.device_name} is not accepting files."
            )

    def _prepare(self, tid: str, ids: list[str]) -> tuple[list[tuple[Path, str]], Manifest]:
        files: list[File] = []
        entries: list[tuple[Path, str]] = []
        with self.core.db.session() as s:
            repo = FileRepository(s)
            for fid in ids:
                f = repo.get_active(fid)
                if f is None:
                    raise HandOffError("FILE_NOT_FOUND", "A selected file no longer exists.")
                path = self.core.files.file_path(fid)
                if path.stat().st_size != f.size_bytes:
                    raise HandOffError("FILE_STORAGE_ERROR", f"{f.original_name} changed on disk.")
                files.append(f)
                entries.append((path, f.original_name))
        return entries, build_manifest(tid, files)

    # ----- background job ----------------------------------------------------------------

    def _run(
        self, tid: str, manifest: Manifest, entries: list[tuple[Path, str]], peer: ActivePeer
    ) -> None:
        self._abort.clear()
        self._abort_reason = None
        archive = self.core.paths.temp_dir / f"{tid}.zip"
        try:
            address, port = peer.endpoint()
            self._set_status(tid, TransferStatus.VALIDATING)
            payload = {
                **manifest.to_dict(),
                "source_device_id": self.core.identity.device_id,
                "file_count": len(manifest.files),
                "total_size": manifest.total_size,
                "archive_name": f"handoff-{tid}.zip",
            }
            resp = self.client.request(
                address, port, "POST", "/api/v1/transfers",
                expected_key=peer.public_key, json=payload, headers={"Idempotency-Key": tid},
            )  # fmt: skip
            if resp.status_code not in (200, 201):
                raise error_from_response(resp)
            self._set_status(tid, TransferStatus.ACCEPTED)

            size = build_archive(entries, archive)
            self._set_status(tid, TransferStatus.TRANSFERRING, archive_size=size)
            self._finish(tid, self._upload(peer, tid, archive, size))
        except HandOffError as exc:
            self._fail(tid, exc)
        except Exception:
            log.exception("Transfer %s failed unexpectedly", tid)
            self._fail(tid, HandOffError("INTERNAL_ERROR", "The transfer could not be completed."))
        finally:
            archive.unlink(missing_ok=True)
            self._ensure_terminal(tid)

    def _chunks(self, path: Path, tid: str) -> Iterator[bytes]:
        sent, last = 0, 0.0
        with path.open("rb") as f:
            while chunk := f.read(IO_CHUNK_SIZE):
                if self._abort.is_set():
                    raise self._abort_reason or HandOffError(
                        "DEVICE_OFFLINE", "The device went offline."
                    )
                yield chunk
                sent += len(chunk)
                now = time.monotonic()
                if now - last >= _PROGRESS_INTERVAL:
                    self._progress(tid, sent)
                    last = now

    def _upload(self, peer: ActivePeer, tid: str, archive: Path, size: int) -> httpx.Response:
        address, port = peer.endpoint()
        try:
            return self.client.request(
                address, port, "POST", f"/api/v1/transfers/{tid}/data",
                expected_key=peer.public_key, content=self._chunks(archive, tid),
                headers={"Content-Type": "application/zip", "Content-Length": str(size)},
                timeout=httpx.Timeout(
                    connect=CONNECT_TIMEOUT_SECONDS, read=UPLOAD_RESPONSE_TIMEOUT_SECONDS,
                    write=REQUEST_TIMEOUT_SECONDS * 4, pool=CONNECT_TIMEOUT_SECONDS,
                ),
            )  # fmt: skip
        except HandOffError as exc:
            if self._abort.is_set() and self._abort_reason is not None:
                raise self._abort_reason from exc
            raise

    # ----- state updates -----------------------------------------------------------------

    def _set_status(
        self, tid: str, status: TransferStatus, *, archive_size: int | None = None
    ) -> None:
        with self.core.db.session() as s:
            repo = TransferRepository(s)
            t = must(repo.get(tid), "transfer")
            repo.set_status(t, status)
            if archive_size is not None:
                t.archive_size_bytes = archive_size
            if status is TransferStatus.TRANSFERRING:
                for tf in t.files:
                    tf.status = FileStatus.TRANSFERRING.value
                record_event(
                    s, AuditEvent.TRANSFER_STARTED, "Sending files.",
                    device_id=t.destination_device_id, transfer_id=tid,
                )  # fmt: skip

    def _progress(self, tid: str, sent: int) -> None:
        with self.core.db.session() as s:
            t = TransferRepository(s).get(tid)
            if t is not None and t.status == TransferStatus.TRANSFERRING.value:
                t.bytes_transferred = sent

    def _finish(self, tid: str, resp: httpx.Response) -> None:
        if resp.status_code != 200:
            raise error_from_response(resp)
        try:
            body = resp.json()
            reported = {
                str(f["name"]): (FileStatus(f["status"]), f.get("failure_code"))
                for f in body["files"]
            }
        except (ValueError, KeyError, TypeError) as exc:
            raise HandOffError("NETWORK_ERROR", "Unexpected response from the device.") from exc
        with self.core.db.session() as s:
            repo = TransferRepository(s)
            t = must(repo.get(tid), "transfer")
            if {tf.original_name for tf in t.files} != set(reported):
                raise HandOffError("NETWORK_ERROR", "The device reported different files.")
            now = utcnow()
            for tf in t.files:
                status, code = reported[tf.original_name]
                tf.status, tf.completed_at = status.value, now
                tf.failure_code = (
                    None if status is FileStatus.COMPLETED else str(code or "TRANSFER_FAILED")[:64]
                )
            final = derive_job_status([FileStatus(tf.status) for tf in t.files])
            if body.get("status") != final.value:
                raise HandOffError("NETWORK_ERROR", "The device's result was inconsistent.")
            t.bytes_transferred = t.archive_size_bytes or t.bytes_transferred
            failed = [tf for tf in t.files if tf.status == FileStatus.FAILED.value]
            repo.set_status(
                t, final,
                error_code=failed[0].failure_code if failed else None,
                error_message="Some files failed verification." if failed else None,
            )  # fmt: skip
            event = (
                AuditEvent.TRANSFER_COMPLETED
                if final is TransferStatus.COMPLETED
                else AuditEvent.TRANSFER_PARTIALLY_COMPLETED
            )
            record_event(
                s, event, f"Transfer {final.value.replace('_', ' ')}.",
                device_id=t.destination_device_id, transfer_id=tid,
                metadata={"failed_files": [tf.original_name for tf in failed]},
            )  # fmt: skip

    def _fail(self, tid: str, exc: HandOffError) -> None:
        with self.core.db.session() as s:
            repo = TransferRepository(s)
            t = repo.get(tid)
            if t is None or TransferStatus(t.status) in TERMINAL_STATUSES:
                return
            reported = exc.details.get("files")
            by_name = (
                {f.get("name"): f for f in reported if isinstance(f, dict)}
                if isinstance(reported, list)
                else {}
            )
            now = utcnow()
            for tf in t.files:
                info = by_name.get(tf.original_name)
                if info and info.get("status") in ("completed", "failed"):
                    tf.status = str(info["status"])
                    tf.failure_code = (
                        None
                        if tf.status == "completed"
                        else str(info.get("failure_code") or exc.code)[:64]
                    )
                elif tf.status in (FileStatus.PENDING.value, FileStatus.TRANSFERRING.value):
                    tf.status, tf.failure_code = FileStatus.FAILED.value, exc.code
                    tf.failure_message = exc.message[:300]
                tf.completed_at = now
            repo.set_status(
                t, TransferStatus.FAILED, error_code=exc.code, error_message=exc.message[:300]
            )
            record_event(
                s, AuditEvent.TRANSFER_FAILED, f"Transfer failed: {exc.message}",
                device_id=t.destination_device_id, transfer_id=tid, metadata={"code": exc.code},
            )  # fmt: skip

    def _ensure_terminal(self, tid: str) -> None:
        """Safety net: a job must never be left in an active state."""
        with self.core.db.session() as s:
            t = TransferRepository(s).get(tid)
            active = t is not None and TransferStatus(t.status) not in TERMINAL_STATUSES
        if active:
            self._fail(tid, HandOffError("INTERNAL_ERROR", "The transfer ended unexpectedly."))

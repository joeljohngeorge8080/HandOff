"""Managed files: import (copy), list, logical delete (FR-001..FR-008, ADR-022/023/049)."""

from __future__ import annotations

import hashlib
import logging
import mimetypes
import os
import uuid
from pathlib import Path
from typing import Any

from handoff.audit import REJECTION_EVENTS, AuditEvent, record_event
from handoff.config import IO_CHUNK_SIZE, MAX_FILE_SIZE
from handoff.db.engine import Database
from handoff.db.models import File, iso, utcnow
from handoff.db.repositories import FileRepository
from handoff.errors import HandOffError
from handoff.files.naming import unique_display_name
from handoff.files.validation import validate_extension, validate_filename, validate_size
from handoff.paths import AppPaths

log = logging.getLogger(__name__)


def serialize_file(f: File) -> dict[str, Any]:
    return {
        "id": f.id,
        "name": f.original_name,
        "size": f.size_bytes,
        "extension": f.extension,
        "source": f.source,
        "created_at": iso(f.created_at),
    }


class FileManager:
    def __init__(self, paths: AppPaths, db: Database) -> None:
        self.paths, self.db = paths, db

    # ----- import -------------------------------------------------------------------------

    def import_file(self, source: str | os.PathLike[str]) -> dict[str, Any]:
        """Copy a user file into HandOff storage. The original is only ever read."""
        src = Path(source)
        name = src.name
        try:
            validate_filename(name)
            ext = validate_extension(name)
            size = self._stat_regular_file(src)
            validate_size(size, name)
        except HandOffError as exc:
            self._audit_rejection(exc, name)
            raise

        file_id = str(uuid.uuid4())
        tmp = self.paths.temp_dir / f"import-{file_id}.part"
        final = self.paths.files_dir / file_id
        try:
            copied, digest = self._copy_and_hash(src, tmp, name)
            os.replace(tmp, final)
        except BaseException:
            tmp.unlink(missing_ok=True)
            final.unlink(missing_ok=True)
            raise

        try:
            with self.db.session() as s:
                repo = FileRepository(s)
                now = utcnow()
                row = repo.add(
                    File(
                        id=file_id,
                        original_name=unique_display_name(name, repo.active_names()),
                        stored_name=file_id,
                        extension=ext,
                        mime_type=mimetypes.guess_type(name)[0],
                        size_bytes=copied,
                        sha256=digest,  # ADR-049: hashed once, when the file enters storage
                        source="imported",
                        storage_path=self.paths.relative(final),
                        created_at=now,
                        updated_at=now,
                    )
                )
                record_event(
                    s,
                    AuditEvent.FILE_IMPORTED,
                    f"Imported {row.original_name}.",
                    file_id=file_id,
                    metadata={"file_name": row.original_name, "size_bytes": copied},
                )
                result = serialize_file(row)
        except BaseException:
            final.unlink(missing_ok=True)
            raise
        return result

    @staticmethod
    def _stat_regular_file(src: Path) -> int:
        try:
            st = src.stat()
        except FileNotFoundError as exc:
            raise HandOffError("FILE_NOT_FOUND", "The selected file no longer exists.") from exc
        except OSError as exc:
            raise HandOffError("FILE_STORAGE_ERROR", "The selected file cannot be read.") from exc
        if not src.is_file():
            raise HandOffError("INVALID_FILE", "Only regular files can be added.")
        return st.st_size

    @staticmethod
    def _copy_and_hash(src: Path, dest: Path, name: str) -> tuple[int, str]:
        """Stream-copy with a hard byte cap, so a file that grows mid-copy is still bounded."""
        h = hashlib.sha256()
        total = 0
        try:
            with src.open("rb") as fin, dest.open("wb") as fout:
                while chunk := fin.read(IO_CHUNK_SIZE):
                    total += len(chunk)
                    if total > MAX_FILE_SIZE:
                        validate_size(total, name)
                    h.update(chunk)
                    fout.write(chunk)
                fout.flush()
                os.fsync(fout.fileno())
        except OSError as exc:
            log.error("Copy of %s failed: %s", name, exc)
            raise HandOffError("FILE_STORAGE_ERROR", "The file could not be copied.") from exc
        return total, h.hexdigest()

    def _audit_rejection(self, exc: HandOffError, name: str) -> None:
        event = REJECTION_EVENTS.get(exc.code)
        if event is None:
            return
        with self.db.session() as s:
            record_event(
                s,
                event,
                f"File rejected: {exc.message}",
                metadata={"file_name": name[:120], "code": exc.code},
            )

    # ----- read ---------------------------------------------------------------------------

    def list_files(self) -> list[dict[str, Any]]:
        with self.db.session() as s:
            return [serialize_file(f) for f in FileRepository(s).list_active()]

    def get_file(self, file_id: str) -> dict[str, Any]:
        with self.db.session() as s:
            f = FileRepository(s).get_active(file_id)
            if f is None:
                raise HandOffError("FILE_NOT_FOUND", "File not found.")
            return serialize_file(f)

    def file_path(self, file_id: str) -> Path:
        """Absolute path of an active managed file (for the sender), confined to storage."""
        with self.db.session() as s:
            f = FileRepository(s).get_active(file_id)
            if f is None:
                raise HandOffError("FILE_NOT_FOUND", "File not found.")
            path = self.paths.resolve_storage(f.storage_path)
        if not path.is_file():
            raise HandOffError("FILE_STORAGE_ERROR", "The stored file is missing.")
        return path

    # ----- delete -------------------------------------------------------------------------

    def delete_file(self, file_id: str) -> None:
        """Logical delete (ADR-023): the record, history and audit rows stay; the copy goes."""
        with self.db.session() as s:
            repo = FileRepository(s)
            f = repo.get_active(file_id)
            if f is None:
                raise HandOffError("FILE_NOT_FOUND", "File not found.")
            storage = self.paths.resolve_storage(f.storage_path)
            repo.mark_deleted(f)
            record_event(
                s,
                AuditEvent.FILE_DELETED,
                f"Deleted {f.original_name}.",
                file_id=file_id,
                metadata={"file_name": f.original_name},
            )
        # Database first: a leftover file is harmless, a record without its file is not.
        try:
            storage.unlink(missing_ok=True)
        except OSError as exc:
            log.error("Could not remove stored file for %s: %s", file_id, exc)
            raise HandOffError(
                "FILE_STORAGE_ERROR", "The file was removed from HandOff but its data remains."
            ) from exc

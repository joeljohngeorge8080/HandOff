"""Transfer manifest (ADR-051): the sender's stored name, size and SHA-256 for every file.

`Manifest.from_dict` is the receiver's first line of defense: everything in it came
over the network and is untrusted until validated here.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from handoff.config import MAX_FILES_PER_TRANSFER
from handoff.db.models import File
from handoff.errors import HandOffError
from handoff.files.validation import validate_extension, validate_filename, validate_size

_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _invalid(message: str) -> HandOffError:
    return HandOffError("TRANSFER_VALIDATION_FAILED", message)


@dataclass(frozen=True)
class ManifestFile:
    file_id: str
    filename: str
    size: int
    sha256: str


@dataclass(frozen=True)
class Manifest:
    transfer_id: str
    files: tuple[ManifestFile, ...]

    @property
    def total_size(self) -> int:
        return sum(f.size for f in self.files)

    def to_dict(self) -> dict[str, Any]:
        return {
            "transfer_id": self.transfer_id,
            "files": [
                {"file_id": f.file_id, "filename": f.filename, "size": f.size, "sha256": f.sha256}
                for f in self.files
            ],
        }

    @classmethod
    def from_dict(cls, data: object) -> Manifest:
        if not isinstance(data, dict):
            raise _invalid("Manifest must be an object.")
        transfer_id = data.get("transfer_id")
        if not isinstance(transfer_id, str) or not _ID.match(transfer_id):
            raise _invalid("Manifest has an invalid transfer_id.")
        raw_files = data.get("files")
        if not isinstance(raw_files, list) or not raw_files:
            raise _invalid("Manifest must list at least one file.")
        if len(raw_files) > MAX_FILES_PER_TRANSFER:
            raise _invalid(f"A transfer may contain at most {MAX_FILES_PER_TRANSFER} files.")

        files: list[ManifestFile] = []
        seen: set[str] = set()
        for raw in raw_files:
            if not isinstance(raw, dict):
                raise _invalid("Manifest file entry must be an object.")
            file_id, filename = raw.get("file_id"), raw.get("filename")
            size, sha256 = raw.get("size"), raw.get("sha256")
            if not isinstance(file_id, str) or not _ID.match(file_id):
                raise _invalid("Manifest file has an invalid file_id.")
            if not isinstance(sha256, str) or not _SHA256.match(sha256):
                raise _invalid("Manifest file has an invalid sha256.")
            # These raise the specific codes (INVALID_PATH, FILE_TOO_LARGE, ...) for auditing.
            validate_filename(filename)  # type: ignore[arg-type]
            validate_extension(filename)  # type: ignore[arg-type]
            validate_size(size, filename)  # type: ignore[arg-type]
            key = filename.casefold()  # type: ignore[union-attr]
            if key in seen:
                raise _invalid("Manifest contains duplicate file names.")
            seen.add(key)
            files.append(ManifestFile(file_id, filename, size, sha256))  # type: ignore[arg-type]
        return cls(transfer_id, tuple(files))


def build_manifest(transfer_id: str, files: Sequence[File]) -> Manifest:
    """Built by the backend from stored metadata only; never from client-supplied values."""
    return Manifest(
        transfer_id,
        tuple(ManifestFile(f.id, f.original_name, f.size_bytes, f.sha256) for f in files),
    )

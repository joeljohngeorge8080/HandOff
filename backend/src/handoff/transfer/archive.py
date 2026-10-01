"""Transfer archive: ZIP build (sender) and safe extraction (receiver).

Safe extraction rules (SECURITY §24-26):
* every entry name is validated *before* anything is written, and any bad entry rejects
  the whole transfer;
* entry names are never used as filesystem paths: files are written to `<index>.part`
  inside the target directory, so an entry cannot escape it by construction;
* the archive must contain exactly the files in the manifest;
* per-file size and SHA-256 failures are recorded per file (partial completion).
"""

from __future__ import annotations

import hashlib
import shutil
import stat
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from handoff.config import IO_CHUNK_SIZE
from handoff.errors import HandOffError
from handoff.files.validation import validate_filename
from handoff.transfer.manifest import Manifest, ManifestFile

_FIXED_DATE = (2020, 1, 1, 0, 0, 0)  # no timestamps leaked, no pre-1980 mtime failures


def build_archive(entries: Sequence[tuple[Path, str]], dest: Path) -> int:
    """Write a flat, uncompressed ZIP of (source_path, name) pairs. Returns its size."""
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as zf:
        for path, name in entries:
            validate_filename(name)
            info = zipfile.ZipInfo(name, date_time=_FIXED_DATE)
            info.compress_type = zipfile.ZIP_STORED
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            with path.open("rb") as src, zf.open(info, "w", force_zip64=True) as out:
                shutil.copyfileobj(src, out, IO_CHUNK_SIZE)
    return dest.stat().st_size


@dataclass
class ExtractedFile:
    manifest: ManifestFile
    path: Path | None
    ok: bool
    failure_code: str | None = None
    failure_message: str | None = None
    actual_sha256: str | None = None


def _reject(message: str, code: str = "TRANSFER_ARCHIVE_INVALID") -> HandOffError:
    return HandOffError(code, message)


def _check_structure(zf: zipfile.ZipFile, manifest: Manifest) -> dict[str, zipfile.ZipInfo]:
    infos = zf.infolist()
    by_name: dict[str, zipfile.ZipInfo] = {}
    for info in infos:
        name = info.orig_filename  # raw name: no OS-specific separator translation
        validate_filename(name)  # INVALID_PATH / INVALID_FILE reject the whole transfer
        if info.flag_bits & 0x1:
            raise _reject("Encrypted archive entries are not allowed.")
        if stat.S_IFMT(info.external_attr >> 16) not in (0, stat.S_IFREG):
            raise _reject("Archive entries must be regular files.")
        if name in by_name:
            raise _reject("Archive contains duplicate entries.")
        by_name[name] = info
    if set(by_name) != {f.filename for f in manifest.files} or len(infos) != len(manifest.files):
        raise _reject(
            "Archive contents do not match the transfer manifest.", "TRANSFER_VALIDATION_FAILED"
        )
    return by_name


def _extract_one(zf: zipfile.ZipFile, info: zipfile.ZipInfo, mf: ManifestFile, target: Path) -> str:
    """Stream one entry to `target`, bounded by the manifest size. Returns its SHA-256."""
    h = hashlib.sha256()
    written = 0
    with zf.open(info) as src, target.open("wb") as out:
        while chunk := src.read(IO_CHUNK_SIZE):
            written += len(chunk)
            if written > mf.size:
                raise HandOffError("TRANSFER_VALIDATION_FAILED", "File is larger than declared.")
            h.update(chunk)
            out.write(chunk)
    if written != mf.size:
        raise HandOffError("TRANSFER_VALIDATION_FAILED", "File is smaller than declared.")
    return h.hexdigest()


def safe_extract(zip_path: Path, dest_dir: Path, manifest: Manifest) -> list[ExtractedFile]:
    """Validate then extract. Raises HandOffError to reject the whole transfer."""
    try:
        zf = zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise _reject("The transfer archive is not a valid ZIP file.") from exc

    with zf:
        by_name = _check_structure(zf, manifest)
        dest_dir.mkdir(parents=True, exist_ok=True)
        root = dest_dir.resolve()
        results: list[ExtractedFile] = []
        for index, mf in enumerate(manifest.files):
            target = (root / f"{index}.part").resolve()
            if target.parent != root:  # defense in depth; the name is index-based
                raise _reject("Destination escaped the transfer directory.", "INVALID_PATH")
            try:
                actual = _extract_one(zf, by_name[mf.filename], mf, target)
            except HandOffError as exc:
                target.unlink(missing_ok=True)
                results.append(ExtractedFile(mf, None, False, exc.code, exc.message))
                continue
            except (
                zipfile.BadZipFile,
                EOFError,
                OSError,
                zipfile.LargeZipFile,
                RuntimeError,
            ) as exc:
                target.unlink(missing_ok=True)
                crc = "CRC" in str(exc)
                code = "INVALID_HASH" if crc else "TRANSFER_ARCHIVE_EXTRACTION_FAILED"
                results.append(ExtractedFile(mf, None, False, code, "File data is corrupted."))
                continue
            if actual != mf.sha256:
                target.unlink(missing_ok=True)
                results.append(
                    ExtractedFile(
                        mf, None, False, "INVALID_HASH", "SHA-256 verification failed.", actual
                    )
                )
            else:
                results.append(ExtractedFile(mf, target, True, actual_sha256=actual))
        return results

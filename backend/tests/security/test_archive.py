import hashlib
import warnings
import zipfile
from pathlib import Path

import pytest

from handoff.errors import HandOffError
from handoff.transfer.archive import build_archive, safe_extract
from handoff.transfer.manifest import Manifest, ManifestFile


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def manifest_for(files: dict[str, bytes]) -> Manifest:
    return Manifest(
        "tr_1",
        tuple(ManifestFile(f"f{i}", n, len(d), sha(d)) for i, (n, d) in enumerate(files.items())),
    )


def make_zip(path: Path, entries: dict[str, bytes], *, store: bool = True) -> Path:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED if store else zipfile.ZIP_DEFLATED) as z:
            for name, data in entries.items():
                z.writestr(name, data)
    return path


FILES = {"photo.jpg": b"jpeg-bytes", "video.mp4": b"v" * 5000, "notes.txt": b"hello"}


def test_build_then_extract_round_trip(tmp_path):
    src = {}
    for name, data in FILES.items():
        p = tmp_path / f"src_{name}"
        p.write_bytes(data)
        src[name] = p
    archive = tmp_path / "payload.zip"
    size = build_archive([(p, n) for n, p in src.items()], archive)
    assert size == archive.stat().st_size > 0

    results = safe_extract(archive, tmp_path / "out", manifest_for(FILES))

    assert [r.ok for r in results] == [True, True, True]
    for r, (name, data) in zip(results, FILES.items(), strict=True):
        assert r.manifest.filename == name
        assert r.path.read_bytes() == data
        assert r.path.parent == (tmp_path / "out").resolve()


def test_archive_preserves_names_and_is_a_standard_flat_zip(tmp_path):
    p = tmp_path / "a.txt"
    p.write_bytes(b"x")
    archive = tmp_path / "p.zip"
    build_archive([(p, "a.txt")], archive)
    with zipfile.ZipFile(archive) as z:
        assert z.namelist() == ["a.txt"]
        assert z.infolist()[0].compress_type == zipfile.ZIP_STORED
        assert z.testzip() is None


def test_build_refuses_unsafe_entry_names(tmp_path):
    p = tmp_path / "a.txt"
    p.write_bytes(b"x")
    with pytest.raises(HandOffError):
        build_archive([(p, "../evil.txt")], tmp_path / "p.zip")


@pytest.mark.parametrize(
    "evil",
    [
        "../../outside.txt",
        "../../../outside.txt",
        "..\\..\\outside.txt",
        "/etc/passwd",
        "C:\\Windows\\System32\\test.exe",
        "sub/dir.txt",
        "sub\\dir.txt",
        "..",
        "dir/",
    ],
)
def test_malicious_entry_names_reject_the_whole_transfer_and_write_nothing(tmp_path, evil):
    entries = {"good.txt": b"fine", evil: b"pwned"}
    archive = make_zip(tmp_path / "payload.zip", entries)
    out = tmp_path / "work" / "out"
    manifest = manifest_for({"good.txt": b"fine", "other.txt": b"x"})

    with pytest.raises(HandOffError) as e:
        safe_extract(archive, out, manifest)

    assert e.value.code in {"INVALID_PATH", "INVALID_FILE"}
    assert not (tmp_path / "outside.txt").exists()
    assert not (tmp_path / "work" / "outside.txt").exists()
    assert not out.exists() or list(out.iterdir()) == []  # not even the good file was extracted


def test_traversal_never_escapes_even_with_a_matching_manifest(tmp_path):
    # A hostile manifest that "agrees" with the hostile archive is stopped at validation.
    archive = make_zip(tmp_path / "p.zip", {"../../outside.txt": b"x"})
    manifest = Manifest("t", (ManifestFile("f", "../../outside.txt", 1, sha(b"x")),))
    with pytest.raises(HandOffError):
        safe_extract(archive, tmp_path / "out", manifest)
    assert not (tmp_path.parent / "outside.txt").exists()


def test_extra_entry_not_in_manifest_is_rejected(tmp_path):
    archive = make_zip(tmp_path / "p.zip", {**FILES, "sneaky.txt": b"x"})
    with pytest.raises(HandOffError) as e:
        safe_extract(archive, tmp_path / "out", manifest_for(FILES))
    assert e.value.code == "TRANSFER_VALIDATION_FAILED"


def test_missing_entry_is_rejected(tmp_path):
    partial = {k: v for k, v in FILES.items() if k != "notes.txt"}
    archive = make_zip(tmp_path / "p.zip", partial)
    with pytest.raises(HandOffError) as e:
        safe_extract(archive, tmp_path / "out", manifest_for(FILES))
    assert e.value.code == "TRANSFER_VALIDATION_FAILED"


def test_renamed_entry_is_rejected(tmp_path):
    archive = make_zip(tmp_path / "p.zip", {"a.txt": b"x"})
    with pytest.raises(HandOffError) as e:
        safe_extract(archive, tmp_path / "out", manifest_for({"b.txt": b"x"}))
    assert e.value.code == "TRANSFER_VALIDATION_FAILED"


def test_duplicate_entries_are_rejected(tmp_path):
    path = tmp_path / "p.zip"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("a.txt", b"one")
            z.writestr("a.txt", b"two")
    with pytest.raises(HandOffError) as e:
        safe_extract(path, tmp_path / "out", manifest_for({"a.txt": b"one"}))
    assert e.value.code in {"TRANSFER_ARCHIVE_INVALID", "TRANSFER_VALIDATION_FAILED"}


def test_symlink_entries_are_rejected(tmp_path):
    path = tmp_path / "p.zip"
    with zipfile.ZipFile(path, "w") as z:
        info = zipfile.ZipInfo("link.txt")
        info.external_attr = (0o120777) << 16
        z.writestr(info, "/etc/passwd")
    with pytest.raises(HandOffError) as e:
        safe_extract(path, tmp_path / "out", manifest_for({"link.txt": b"/etc/passwd"}))
    assert e.value.code == "TRANSFER_ARCHIVE_INVALID"


def test_encrypted_entries_are_rejected(tmp_path):
    path = make_zip(tmp_path / "p.zip", {"a.txt": b"x"})
    raw = bytearray(path.read_bytes())
    raw[6] |= 0x1  # general-purpose flag "encrypted" in the local file header
    raw[raw.index(b"PK\x01\x02") + 8] |= 0x1  # ...and in the central directory
    path.write_bytes(bytes(raw))
    with zipfile.ZipFile(path) as z:  # sanity: the crafted archive really is flagged
        assert z.infolist()[0].flag_bits & 0x1
    with pytest.raises(HandOffError) as e:
        safe_extract(path, tmp_path / "out", manifest_for({"a.txt": b"x"}))
    assert e.value.code == "TRANSFER_ARCHIVE_INVALID"


@pytest.mark.parametrize("content", [b"", b"not a zip at all", b"PK\x03\x04garbage"])
def test_non_zip_payload_is_rejected(tmp_path, content):
    p = tmp_path / "p.zip"
    p.write_bytes(content)
    with pytest.raises(HandOffError) as e:
        safe_extract(p, tmp_path / "out", manifest_for({"a.txt": b"x"}))
    assert e.value.code == "TRANSFER_ARCHIVE_INVALID"


def test_content_that_does_not_match_its_hash_fails_only_that_file(tmp_path):
    expected = dict(FILES)
    tampered = dict(FILES)
    tampered["video.mp4"] = b"X" * 5000  # same size, different bytes
    archive = make_zip(tmp_path / "p.zip", tampered)

    results = {
        r.manifest.filename: r
        for r in safe_extract(archive, tmp_path / "out", manifest_for(expected))
    }

    assert results["photo.jpg"].ok and results["notes.txt"].ok
    bad = results["video.mp4"]
    assert not bad.ok and bad.failure_code == "INVALID_HASH" and bad.path is None
    assert bad.actual_sha256 == sha(b"X" * 5000)
    assert not any("1.part" in p.name for p in (tmp_path / "out").iterdir())  # bad file removed


def test_file_larger_than_declared_is_stopped_and_fails(tmp_path):
    archive = make_zip(tmp_path / "p.zip", {"a.txt": b"A" * 1000})
    declared = Manifest("t", (ManifestFile("f", "a.txt", 10, sha(b"A" * 1000)),))
    (r,) = safe_extract(archive, tmp_path / "out", declared)
    assert not r.ok and r.failure_code == "TRANSFER_VALIDATION_FAILED"
    assert list((tmp_path / "out").iterdir()) == []


def test_file_smaller_than_declared_fails(tmp_path):
    archive = make_zip(tmp_path / "p.zip", {"a.txt": b"AAA"})
    declared = Manifest("t", (ManifestFile("f", "a.txt", 100, sha(b"AAA")),))
    (r,) = safe_extract(archive, tmp_path / "out", declared)
    assert not r.ok and r.failure_code == "TRANSFER_VALIDATION_FAILED"


def test_corrupted_zip_data_is_a_per_file_failure_not_a_crash(tmp_path):
    data = b"B" * 4000
    archive = make_zip(tmp_path / "p.zip", {"a.txt": data})
    raw = bytearray(archive.read_bytes())
    raw[raw.index(b"BBBB") + 10] ^= 0xFF  # flip a payload byte; the CRC no longer matches
    archive.write_bytes(bytes(raw))
    (r,) = safe_extract(archive, tmp_path / "out", manifest_for({"a.txt": data}))
    assert not r.ok and r.failure_code == "INVALID_HASH"


def test_decompression_bomb_is_bounded_by_the_manifest(tmp_path):
    bomb = b"\x00" * (5 * 1024 * 1024)
    archive = make_zip(tmp_path / "p.zip", {"a.txt": bomb}, store=False)
    assert archive.stat().st_size < 50_000  # tiny on disk
    declared = Manifest("t", (ManifestFile("f", "a.txt", 100, sha(bomb)),))
    (r,) = safe_extract(archive, tmp_path / "out", declared)
    assert not r.ok
    assert sum(p.stat().st_size for p in (tmp_path / "out").iterdir()) == 0


def test_extraction_names_files_by_index_never_by_archive_name(tmp_path):
    archive = make_zip(tmp_path / "p.zip", {"photo.jpg": b"x"})
    (r,) = safe_extract(archive, tmp_path / "out", manifest_for({"photo.jpg": b"x"}))
    assert r.path.name == "0.part"


def test_extraction_stops_reading_at_the_declared_size(tmp_path, monkeypatch):
    """A lying archive must not make us hash or write more than the manifest allows."""
    import hashlib as real_hashlib

    import handoff.transfer.archive as archive_mod

    fed = {"bytes": 0}
    real_sha256 = real_hashlib.sha256

    class CountingHash:
        def __init__(self):
            self._h = real_sha256()

        def update(self, data):
            fed["bytes"] += len(data)
            self._h.update(data)

        def hexdigest(self):
            return self._h.hexdigest()

    bomb = b"\x00" * (3 * 1024 * 1024)
    archive = make_zip(tmp_path / "p.zip", {"a.txt": bomb}, store=False)
    declared = Manifest("t", (ManifestFile("f", "a.txt", 2048, sha(bomb)),))
    monkeypatch.setattr(archive_mod.hashlib, "sha256", CountingHash)
    monkeypatch.setattr(archive_mod, "IO_CHUNK_SIZE", 1024)
    (r,) = safe_extract(archive, tmp_path / "out", declared)
    assert not r.ok
    assert fed["bytes"] <= 2048  # stopped at the declared size, never read the 3 MiB

import hashlib
import sys
from pathlib import Path

import pytest
from helpers import add_peer, add_transfer
from sqlalchemy import select

from handoff import config
from handoff.audit import AuditEvent
from handoff.db.models import AuditLog, File, Transfer, TransferFile
from handoff.db.repositories import AuditRepository
from handoff.errors import HandOffError
from handoff.files.manager import FileManager

MB = 1024 * 1024


def _audit(core, event):
    with core.db.session() as s:
        return AuditRepository(s).list(event_type=event.value)


def _sparse(path: Path, size: int) -> Path:
    with path.open("wb") as f:
        f.truncate(size)
    return path


@pytest.mark.parametrize("name", ["notes.txt", "photo.jpg", "scan.jpeg", "pic.png", "doc.pdf"])
def test_import_copies_each_allowed_type_and_records_metadata(core, make_file, name):
    content = b"some bytes for " + name.encode()
    src = make_file(name, content)
    rec = core.files.import_file(src)

    assert rec["name"] == name and rec["size"] == len(content) and rec["source"] == "imported"
    with core.db.session() as s:
        row = s.get(File, rec["id"])
        stored = core.paths.resolve_storage(row.storage_path)
        assert stored.read_bytes() == content
        assert stored != src
        assert row.sha256 == hashlib.sha256(content).hexdigest()
        assert row.stored_name == row.id  # stored under a UUID, not the user's name
        assert row.storage_path == f"files/{row.id}"
        assert row.extension == Path(name).suffix
    assert src.read_bytes() == content  # original untouched
    assert len(_audit(core, AuditEvent.FILE_IMPORTED)) == 1


def test_import_leaves_no_temporary_files_behind(core, make_file):
    core.files.import_file(make_file("a.txt"))
    assert list(core.paths.temp_dir.iterdir()) == []


@pytest.mark.parametrize("name", ["PHOTO.JPG", "SCAN.JPEG", "NOTES.TXT", "DOC.PDF", "Pic.PnG"])
def test_import_accepts_uppercase_extensions(core, make_file, name):
    assert core.files.import_file(make_file(name))["extension"] == Path(name).suffix.lower()


@pytest.mark.parametrize(
    "name",
    ["setup.exe", "SETUP.EXE", "clip.mp4", "a.docx", "a.zip", "run.sh", "x.bat", "noext", "a.lnk"],
)
def test_import_rejects_unsupported_types_and_stores_nothing(core, make_file, name):
    with pytest.raises(HandOffError) as e:
        core.files.import_file(make_file(name))
    assert e.value.code == "FILE_TYPE_NOT_SUPPORTED"
    assert list(core.paths.files_dir.iterdir()) == []
    assert core.files.list_files() == []
    assert len(_audit(core, AuditEvent.UNSUPPORTED_FILE)) == 1


@pytest.mark.parametrize(
    "size",
    [0, 1, 1000, 49 * MB, int(49.9 * MB), config.MAX_FILE_SIZE],
    ids=["0B", "1B", "1KB", "49MB", "49.9MB", "exactly-50MB"],
)
def test_import_accepts_sizes_up_to_exactly_50_mb(core, tmp_path, size):
    src = _sparse(tmp_path / "big.txt", size)
    rec = core.files.import_file(src)
    assert rec["size"] == size


@pytest.mark.parametrize("size", [config.MAX_FILE_SIZE + 1, 72 * MB])
def test_import_rejects_files_over_50_mb_without_copying(core, tmp_path, size):
    src = _sparse(tmp_path / "huge.png", size)
    with pytest.raises(HandOffError) as e:
        core.files.import_file(src)
    assert e.value.code == "FILE_TOO_LARGE"
    assert e.value.details["maximum_size"] == config.MAX_FILE_SIZE
    assert list(core.paths.files_dir.iterdir()) == []
    assert list(core.paths.temp_dir.iterdir()) == []
    assert len(_audit(core, AuditEvent.FILE_TOO_LARGE)) == 1


def test_a_file_that_grows_during_copy_is_still_bounded(core, tmp_path, monkeypatch):
    monkeypatch.setattr("handoff.files.manager.MAX_FILE_SIZE", 1000)
    monkeypatch.setattr("handoff.files.validation.MAX_FILE_SIZE", 1000)
    src = tmp_path / "grow.txt"
    src.write_bytes(b"x" * 5000)
    monkeypatch.setattr(FileManager, "stat_regular_file", staticmethod(lambda p: 10))
    with pytest.raises(HandOffError) as e:
        core.files.import_file(src)
    assert e.value.code == "FILE_TOO_LARGE"
    assert list(core.paths.files_dir.iterdir()) == []
    assert list(core.paths.temp_dir.iterdir()) == []


def test_missing_source_and_directories_are_rejected(core, tmp_path):
    with pytest.raises(HandOffError) as e:
        core.files.import_file(tmp_path / "gone.txt")
    assert e.value.code == "FILE_NOT_FOUND"
    d = tmp_path / "folder.txt"
    d.mkdir()
    with pytest.raises(HandOffError) as e:
        core.files.import_file(d)
    assert e.value.code == "INVALID_FILE"


@pytest.mark.parametrize("name", ["bad:name.txt", "CON.txt", "trail. .txt."])
def test_import_rejects_unsafe_names(core, make_file, name):
    try:
        src = make_file(name)
    except OSError:
        pytest.skip("filesystem cannot create this name")
    with pytest.raises(HandOffError):
        core.files.import_file(src)
    assert core.files.list_files() == []


def test_duplicate_names_get_numbered_display_names_and_both_are_kept(core, make_file, tmp_path):
    other = tmp_path / "elsewhere"
    other.mkdir()
    a = make_file("photo.jpg", b"first")
    b = other / "photo.jpg"
    b.write_bytes(b"second")
    names = [core.files.import_file(p)["name"] for p in (a, b, a)]
    assert names == ["photo.jpg", "photo(1).jpg", "photo(2).jpg"]
    assert len(list(core.paths.files_dir.iterdir())) == 3


def test_identical_content_is_still_copied_with_the_same_hash(core, make_file):
    src = make_file("same.txt", b"identical")
    first = core.files.import_file(src)
    second = core.files.import_file(src)
    assert first["id"] != second["id"]
    with core.db.session() as s:
        assert s.get(File, first["id"]).sha256 == s.get(File, second["id"]).sha256


def test_sha256_is_deterministic_and_sensitive_to_one_byte(core, make_file):
    a = core.files.import_file(make_file("a.txt", b"abcdef"))
    b = core.files.import_file(make_file("b.txt", b"abcdef"))
    c = core.files.import_file(make_file("c.txt", b"abcdeg"))
    with core.db.session() as s:
        h = {k: s.get(File, v["id"]).sha256 for k, v in dict(a=a, b=b, c=c).items()}
    assert h["a"] == h["b"] != h["c"]


def test_gallery_lists_active_files_in_import_order(core, make_file):
    for n in ("one.txt", "two.txt", "three.txt"):
        core.files.import_file(make_file(n))
    assert [f["name"] for f in core.files.list_files()] == ["one.txt", "two.txt", "three.txt"]


def test_failed_database_write_removes_the_copied_file(core, make_file, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr("handoff.files.manager.record_event", boom)
    with pytest.raises(RuntimeError):
        core.files.import_file(make_file("a.txt"))
    assert list(core.paths.files_dir.iterdir()) == []
    assert core.files.list_files() == []


def test_get_file_and_file_path(core, make_file):
    rec = core.files.import_file(make_file("a.txt", b"data"))
    assert core.files.get_file(rec["id"])["name"] == "a.txt"
    assert core.files.file_path(rec["id"]).read_bytes() == b"data"
    with pytest.raises(HandOffError) as e:
        core.files.get_file("nope")
    assert e.value.code == "FILE_NOT_FOUND"


def test_file_path_refuses_a_tampered_storage_path(core, make_file):
    rec = core.files.import_file(make_file("a.txt"))
    for bad in ("../../etc/passwd", "/etc/passwd", "temp/x", "files/../../x"):
        with core.db.session() as s:
            s.get(File, rec["id"]).storage_path = bad
        with pytest.raises(HandOffError) as e:
            core.files.file_path(rec["id"])
        assert e.value.code == "INVALID_PATH"


def test_file_path_reports_a_missing_stored_file(core, make_file):
    rec = core.files.import_file(make_file("a.txt"))
    (core.paths.files_dir / rec["id"]).unlink()
    with pytest.raises(HandOffError) as e:
        core.files.file_path(rec["id"])
    assert e.value.code == "FILE_STORAGE_ERROR"


# ---------------------------------------------------------------- logical deletion


def test_delete_is_logical_and_never_touches_the_original(core, make_file):
    src = make_file("photo.jpg", b"precious")
    rec = core.files.import_file(src)
    stored = core.files.file_path(rec["id"])

    core.files.delete_file(rec["id"])

    assert src.read_bytes() == b"precious"  # FR-008
    assert not stored.exists()  # the managed copy is gone
    assert core.files.list_files() == []  # gone from the gallery
    with core.db.session() as s:
        row = s.get(File, rec["id"])
        assert row is not None and row.deleted_at is not None  # record kept
    assert len(_audit(core, AuditEvent.FILE_DELETED)) == 1
    assert len(_audit(core, AuditEvent.FILE_IMPORTED)) == 1


def test_deleting_a_file_keeps_transfer_history_and_audit(core, make_file):
    add_peer(core)
    rec = core.files.import_file(make_file("photo.jpg"))
    add_transfer(core, "tr_1", files=[("photo.jpg", "completed", rec["id"])])
    with core.db.session() as s:
        audit_before = len(s.scalars(select(AuditLog)).all())

    core.files.delete_file(rec["id"])

    (item,) = core.history.list()
    assert item["transfer_id"] == "tr_1" and item["files"][0]["name"] == "photo.jpg"
    assert item["peer_device_name"] == "Aaron-Laptop"
    with core.db.session() as s:
        assert s.get(Transfer, "tr_1") is not None
        assert len(s.scalars(select(TransferFile)).all()) == 1
        assert len(s.scalars(select(AuditLog)).all()) > audit_before  # only grew


def test_deleting_twice_or_unknown_is_an_error(core, make_file):
    rec = core.files.import_file(make_file("a.txt"))
    core.files.delete_file(rec["id"])
    for fid in (rec["id"], "unknown"):
        with pytest.raises(HandOffError) as e:
            core.files.delete_file(fid)
        assert e.value.code == "FILE_NOT_FOUND"


def test_display_name_can_be_reused_after_deletion(core, make_file):
    src = make_file("photo.jpg")
    first = core.files.import_file(src)
    core.files.delete_file(first["id"])
    assert core.files.import_file(src)["name"] == "photo.jpg"


def test_file_path_of_unknown_file_is_not_found(core):
    with pytest.raises(HandOffError) as e:
        core.files.file_path("nope")
    assert e.value.code == "FILE_NOT_FOUND"


def test_copy_failure_is_reported_and_cleans_up(core, make_file, monkeypatch):
    def broken_fsync(_fd):
        raise OSError("disk full")

    monkeypatch.setattr("handoff.files.manager.os.fsync", broken_fsync)
    with pytest.raises(HandOffError) as e:
        core.files.import_file(make_file("a.txt"))
    assert e.value.code == "FILE_STORAGE_ERROR"
    assert list(core.paths.files_dir.iterdir()) == []
    assert list(core.paths.temp_dir.iterdir()) == []
    assert core.files.list_files() == []


def test_delete_reports_when_the_stored_data_cannot_be_removed(core, make_file, monkeypatch):
    rec = core.files.import_file(make_file("a.txt"))

    def deny(self, missing_ok=False):
        raise PermissionError("locked")

    monkeypatch.setattr(Path, "unlink", deny)
    with pytest.raises(HandOffError) as e:
        core.files.delete_file(rec["id"])
    assert e.value.code == "FILE_STORAGE_ERROR"
    monkeypatch.undo()
    assert core.files.list_files() == []  # the record is already logically deleted
    with core.db.session() as s:
        assert s.get(File, rec["id"]).deleted_at is not None


# ---------------------------------------------------------------- Phase 2 drop rules (ADR-055)


def test_a_folder_is_rejected_with_a_clear_reason(core, tmp_path):
    folder = tmp_path / "album.jpg"  # even a folder that looks like an image
    folder.mkdir()
    with pytest.raises(HandOffError) as e:
        core.files.import_file(folder)
    assert e.value.code == "INVALID_FILE" and e.value.details["reason"] == "directory"
    assert list(core.paths.files_dir.iterdir()) == []


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_a_symlink_is_rejected_and_never_followed(core, make_file, tmp_path):
    target = make_file("real.txt", b"secret")
    link = tmp_path / "link.txt"
    link.symlink_to(target)
    with pytest.raises(HandOffError) as e:
        core.files.import_file(link)
    assert e.value.code == "INVALID_FILE" and e.value.details["reason"] == "symlink"
    assert list(core.paths.files_dir.iterdir()) == []


def test_a_missing_path_is_reported_as_missing(core, tmp_path):
    with pytest.raises(HandOffError) as e:
        core.files.import_file(tmp_path / "gone.txt")
    assert e.value.code == "FILE_NOT_FOUND" and e.value.details["reason"] == "missing"


@pytest.mark.parametrize(
    ("name", "head"),
    [
        ("photo.jpg", b"MZ\x90\x00\x03"),  # Windows program renamed to .jpg
        ("scan.png", b"\x7fELF\x02\x01\x01"),  # Linux program renamed to .png
        ("notes.txt", b"MZ"),
        ("doc.PDF", b"\x7fELF"),
    ],
)
def test_a_renamed_executable_is_rejected_by_content(core, tmp_path, name, head):
    src = tmp_path / name
    src.write_bytes(head + b"\x00" * 100)
    with pytest.raises(HandOffError) as e:
        core.files.import_file(src)
    assert e.value.code == "FILE_TYPE_NOT_SUPPORTED"
    assert e.value.details["reason"] == "executable_content"
    assert list(core.paths.files_dir.iterdir()) == [] and list(core.paths.temp_dir.iterdir()) == []
    assert src.exists()  # the user's original is never touched
    assert len(_audit(core, AuditEvent.UNSUPPORTED_FILE)) == 1


def test_real_images_and_documents_are_not_mistaken_for_executables(core, tmp_path):
    for name, head in [
        ("a.jpg", b"\xff\xd8\xff\xe0"),
        ("b.png", b"\x89PNG\r\n\x1a\n"),
        ("c.pdf", b"%PDF-1.7"),
        ("d.txt", b""),
        ("e.txt", b"Mission notes"),
    ]:
        p = tmp_path / name
        p.write_bytes(head)
        core.files.import_file(p)


def test_validate_source_checks_everything_without_copying(core, make_file):
    name, ext, size = core.files.validate_source(make_file("photo.JPG", b"abc"))
    assert (name, ext, size) == ("photo.JPG", ".jpg", 3)
    assert list(core.paths.files_dir.iterdir()) == []

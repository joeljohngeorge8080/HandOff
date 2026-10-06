import hashlib
import os
import sys

import pytest

from handoff.destination import (
    copy_verified,
    default_receive_dir,
    reserve_unique_path,
    validate_receive_dir,
)
from handoff.errors import HandOffError


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ----- default -----------------------------------------------------------------------------


def test_default_is_the_os_desktop_when_it_exists(tmp_path, monkeypatch):
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    monkeypatch.setattr("handoff.destination.platformdirs.user_desktop_dir", lambda: str(desktop))
    assert default_receive_dir() == desktop


def test_default_falls_back_to_home_when_desktop_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "handoff.destination.platformdirs.user_desktop_dir", lambda: str(tmp_path / "nope")
    )
    monkeypatch.setattr("handoff.destination.Path.home", lambda: tmp_path)
    assert default_receive_dir() == tmp_path


# ----- validate_receive_dir ----------------------------------------------------------------


def test_accepts_an_existing_writable_directory(tmp_path):
    assert validate_receive_dir(str(tmp_path)) == tmp_path.resolve()


def test_validation_leaves_no_probe_file_behind(tmp_path):
    validate_receive_dir(str(tmp_path))
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("bad", [None, 5, "", "   ", "rel/dir", "./x", "\x00", ["/not-a-string"]])
def test_rejects_non_absolute_or_malformed_values(bad):
    with pytest.raises(HandOffError) as e:
        validate_receive_dir(bad)
    assert e.value.code == "INVALID_PATH"


def test_rejects_parent_traversal_even_if_it_resolves(tmp_path):
    (tmp_path / "a").mkdir()
    with pytest.raises(HandOffError) as e:
        validate_receive_dir(str(tmp_path / "a" / ".." / "a"))
    assert e.value.code == "INVALID_PATH"


def test_rejects_missing_directory(tmp_path):
    with pytest.raises(HandOffError) as e:
        validate_receive_dir(str(tmp_path / "missing"))
    assert e.value.code == "INVALID_PATH"


def test_rejects_a_file(tmp_path):
    f = tmp_path / "f.txt"
    f.write_text("x")
    with pytest.raises(HandOffError) as e:
        validate_receive_dir(str(f))
    assert e.value.code == "INVALID_PATH"


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0, reason="needs POSIX non-root")
def test_rejects_unwritable_directory(tmp_path):
    d = tmp_path / "ro"
    d.mkdir()
    d.chmod(0o500)
    try:
        with pytest.raises(HandOffError) as e:
            validate_receive_dir(str(d))
        assert e.value.code == "INVALID_PATH"
    finally:
        d.chmod(0o700)


def test_rejects_forbidden_roots_and_their_children(tmp_path):
    app = tmp_path / "app"
    (app / "keys").mkdir(parents=True)
    for target in (app, app / "keys"):
        with pytest.raises(HandOffError) as e:
            validate_receive_dir(str(target), forbidden=[app])
        assert e.value.code == "INVALID_PATH"


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_symlink_into_forbidden_root_is_rejected_after_resolving(tmp_path):
    app = tmp_path / "app"
    app.mkdir()
    link = tmp_path / "link"
    link.symlink_to(app)
    with pytest.raises(HandOffError):
        validate_receive_dir(str(link), forbidden=[app])


# ----- reserve_unique_path -----------------------------------------------------------------


def test_reserve_uses_the_plain_name_when_free(tmp_path):
    path, fh = reserve_unique_path(tmp_path, "photo.jpg")
    fh.close()
    assert path == tmp_path / "photo.jpg"
    assert path.exists()


def test_reserve_numbers_collisions_without_overwriting(tmp_path):
    (tmp_path / "photo.jpg").write_bytes(b"original")
    (tmp_path / "photo(1).jpg").write_bytes(b"first")
    path, fh = reserve_unique_path(tmp_path, "photo.jpg")
    fh.close()
    assert path.name == "photo(2).jpg"
    assert (tmp_path / "photo.jpg").read_bytes() == b"original"
    assert (tmp_path / "photo(1).jpg").read_bytes() == b"first"


def test_reserve_handles_names_without_numbers_gap(tmp_path):
    (tmp_path / "a.txt").write_text("x")
    (tmp_path / "a(2).txt").write_text("x")
    path, fh = reserve_unique_path(tmp_path, "a.txt")
    fh.close()
    assert path.name == "a(1).txt"


def test_two_reservations_of_the_same_name_never_collide(tmp_path):
    p1, f1 = reserve_unique_path(tmp_path, "x.png")
    p2, f2 = reserve_unique_path(tmp_path, "x.png")
    f1.close()
    f2.close()
    assert {p1.name, p2.name} == {"x.png", "x(1).png"}


@pytest.mark.parametrize("name", ["../evil.txt", "a/b.txt", "..\\x.txt", "/etc/passwd", ""])
def test_reserve_refuses_names_that_are_not_plain_filenames(tmp_path, name):
    with pytest.raises(HandOffError):
        reserve_unique_path(tmp_path, name)
    assert list(tmp_path.iterdir()) == []


def test_reserve_gives_up_instead_of_looping_forever(tmp_path, monkeypatch):
    monkeypatch.setattr("handoff.destination.MAX_NAME_ATTEMPTS", 3)
    for n in ("a.txt", "a(1).txt", "a(2).txt", "a(3).txt"):
        (tmp_path / n).write_text("x")
    with pytest.raises(HandOffError) as e:
        reserve_unique_path(tmp_path, "a.txt")
    assert e.value.code == "FILE_STORAGE_ERROR"


# ----- copy_verified -----------------------------------------------------------------------


def test_copy_verified_writes_the_file_and_returns_its_final_name(tmp_path):
    src = tmp_path / "src.bin"
    src.write_bytes(b"hello")
    dest = tmp_path / "dest"
    dest.mkdir()
    out = copy_verified(src, dest, "hi.txt", _sha(b"hello"))
    assert out == dest / "hi.txt"
    assert out.read_bytes() == b"hello"


def test_copy_verified_renames_instead_of_overwriting(tmp_path):
    src = tmp_path / "src.bin"
    src.write_bytes(b"new")
    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / "hi.txt").write_bytes(b"precious")
    out = copy_verified(src, dest, "hi.txt", _sha(b"new"))
    assert out.name == "hi(1).txt"
    assert (dest / "hi.txt").read_bytes() == b"precious"


def test_copy_verified_removes_its_own_file_on_hash_mismatch(tmp_path):
    src = tmp_path / "src.bin"
    src.write_bytes(b"tampered")
    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / "keep.txt").write_text("mine")
    with pytest.raises(HandOffError) as e:
        copy_verified(src, dest, "hi.txt", _sha(b"expected"))
    assert e.value.code == "INVALID_HASH"
    assert sorted(p.name for p in dest.iterdir()) == ["keep.txt"]


def test_copy_verified_never_deletes_a_preexisting_file_on_failure(tmp_path):
    src = tmp_path / "src.bin"
    src.write_bytes(b"bad")
    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / "hi.txt").write_bytes(b"existing")
    with pytest.raises(HandOffError):
        copy_verified(src, dest, "hi.txt", _sha(b"other"))
    assert (dest / "hi.txt").read_bytes() == b"existing"
    assert not (dest / "hi(1).txt").exists()


def test_copy_verified_cleans_up_when_the_source_vanishes(tmp_path):
    dest = tmp_path / "dest"
    dest.mkdir()
    with pytest.raises(HandOffError) as e:
        copy_verified(tmp_path / "gone.bin", dest, "hi.txt", _sha(b""))
    assert e.value.code == "FILE_STORAGE_ERROR"
    assert list(dest.iterdir()) == []


def test_copy_verified_handles_empty_files(tmp_path):
    src = tmp_path / "e"
    src.write_bytes(b"")
    dest = tmp_path / "dest"
    dest.mkdir()
    out = copy_verified(src, dest, "empty.txt", _sha(b""))
    assert out.read_bytes() == b""

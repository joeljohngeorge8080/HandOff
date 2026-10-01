import os

import pytest

from handoff.errors import HandOffError
from handoff.paths import AppPaths, default_data_dir


def test_default_data_dir_is_the_os_app_data_location():
    assert default_data_dir().name == "HandOff"
    assert default_data_dir().is_absolute()


def test_layout_matches_database_doc(paths):
    paths.ensure()
    assert paths.db_path.name == "handoff.db"
    for d in (paths.files_dir, paths.received_dir, paths.transfers_dir, paths.temp_dir):
        assert d.is_dir() and d.parent == paths.root


def test_ensure_is_idempotent(paths):
    paths.ensure()
    paths.ensure()


def test_relative_and_resolve_round_trip(paths):
    paths.ensure()
    f = paths.files_dir / "abc"
    f.write_bytes(b"x")
    rel = paths.relative(f)
    assert rel == "files/abc"
    assert paths.resolve_storage(rel) == f.resolve()


@pytest.mark.parametrize(
    "bad", ["", "/etc/passwd", "../x", "files/../../x", "keys/identity.json", "temp/x", "files/.."]
)
def test_resolve_storage_rejects_anything_outside_files_and_received(paths, bad):
    paths.ensure()
    with pytest.raises(HandOffError) as e:
        paths.resolve_storage(bad)
    assert e.value.code == "INVALID_PATH"


@pytest.mark.skipif(os.name != "posix", reason="symlinks")
def test_a_symlink_inside_storage_cannot_lead_outside(paths, tmp_path):
    paths.ensure()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("s")
    (paths.files_dir / "link").symlink_to(outside)
    with pytest.raises(HandOffError) as e:
        paths.resolve_storage("files/link/secret.txt")
    assert e.value.code == "INVALID_PATH"


def test_apppaths_is_immutable(paths):
    with pytest.raises(AttributeError):
        paths.root = "x"  # type: ignore[misc]
    assert isinstance(paths, AppPaths)

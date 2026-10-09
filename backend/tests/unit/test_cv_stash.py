"""Held pictures are written to HandOff's own scratch directory and nowhere else."""

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from handoff.cv.stash import PictureStash

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
NOW = datetime(2026, 10, 9, 11, 30, 45, tzinfo=UTC)


def test_a_picture_is_saved_as_a_dated_png_inside_the_scratch_directory(tmp_path):
    stash = PictureStash(tmp_path / "held", clock=lambda: NOW)
    path = stash.save(PNG)
    assert os.path.dirname(path) == str(tmp_path / "held")
    assert os.path.basename(path) == "image-20261009-113045.png"
    assert Path(path).read_bytes() == PNG


def test_two_pictures_in_the_same_second_never_overwrite_each_other(tmp_path):
    stash = PictureStash(tmp_path / "held", clock=lambda: NOW)
    a, b = stash.save(PNG), stash.save(PNG + b"x")
    assert a != b and Path(a).read_bytes() == PNG and Path(b).read_bytes() == PNG + b"x"


def test_discard_removes_only_files_it_wrote(tmp_path):
    stash = PictureStash(tmp_path / "held", clock=lambda: NOW)
    mine = stash.save(PNG)
    outside = tmp_path / "precious.png"
    outside.write_bytes(b"keep me")
    stash.discard(mine)
    stash.discard(str(outside))  # not in the scratch directory: ignored
    stash.discard(str(tmp_path / "held" / ".." / "precious.png"))  # traversal: ignored
    assert not os.path.exists(mine) and outside.read_bytes() == b"keep me"


def test_discarding_something_already_gone_is_not_an_error(tmp_path):
    stash = PictureStash(tmp_path / "held", clock=lambda: NOW)
    stash.discard(str(tmp_path / "held" / "gone.png"))


def test_the_directory_is_created_on_demand(tmp_path):
    stash = PictureStash(tmp_path / "deep" / "held", clock=lambda: NOW)
    assert os.path.exists(stash.save(PNG))


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions")
def test_saved_pictures_are_private_to_the_user(tmp_path):
    path = PictureStash(tmp_path / "held", clock=lambda: NOW).save(PNG)
    assert oct(os.stat(path).st_mode & 0o777) == "0o600"

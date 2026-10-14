import sys

import pytest

from handoff import opener
from handoff.errors import HandOffError


def test_opens_an_allowed_regular_file(tmp_path, monkeypatch):
    f = tmp_path / "a.png"
    f.write_bytes(b"x")
    seen = []
    monkeypatch.setattr(opener, "_launch", lambda p: seen.append(p))
    opener.open_with_default_app(f)
    assert seen == [f]


@pytest.mark.parametrize("name", ["run.exe", "script.sh", "x.desktop", "noext", "a.png.exe"])
def test_refuses_types_that_are_not_allowed(tmp_path, monkeypatch, name):
    f = tmp_path / name
    f.write_bytes(b"x")
    monkeypatch.setattr(opener, "_launch", lambda p: pytest.fail("must not launch"))
    with pytest.raises(HandOffError):
        opener.open_with_default_app(f)


def test_extension_check_is_case_insensitive(tmp_path, monkeypatch):
    f = tmp_path / "A.PDF"
    f.write_bytes(b"x")
    monkeypatch.setattr(opener, "_launch", lambda p: None)
    opener.open_with_default_app(f)


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_refuses_symlinks_and_missing_files(tmp_path, monkeypatch):
    real = tmp_path / "real.txt"
    real.write_text("x")
    link = tmp_path / "link.txt"
    link.symlink_to(real)
    monkeypatch.setattr(opener, "_launch", lambda p: pytest.fail("must not launch"))
    with pytest.raises(HandOffError):
        opener.open_with_default_app(link)
    with pytest.raises(HandOffError):
        opener.open_with_default_app(tmp_path / "gone.txt")


def test_refuses_a_directory(tmp_path, monkeypatch):
    d = tmp_path / "dir.png"
    d.mkdir()
    monkeypatch.setattr(opener, "_launch", lambda p: pytest.fail("must not launch"))
    with pytest.raises(HandOffError):
        opener.open_with_default_app(d)


@pytest.mark.skipif(sys.platform == "win32", reason="posix launcher")
def test_posix_launch_uses_an_argument_list_without_a_shell(tmp_path, monkeypatch):
    calls = []

    class FakePopen:
        def __init__(self, args, **kw):
            calls.append((args, kw))

    monkeypatch.setattr(opener.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(opener.sys, "platform", "linux")
    f = tmp_path / "a b; rm -rf x.txt"
    opener._launch(f)
    args, kw = calls[0]
    assert args == ["xdg-open", str(f)]
    assert not kw.get("shell")

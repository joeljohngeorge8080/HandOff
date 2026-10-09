"""COPY gesture end to end (ADR-057): grab copies, release sends through the normal pipeline."""

import os

import pytest
from helpers import connect_pair, wait_for

from handoff.cv import clipboard


@pytest.fixture
def pair(node_factory):
    return node_factory("Alice"), node_factory("Bob")


@pytest.fixture
def on_clipboard(monkeypatch):
    def put(paths):
        monkeypatch.setattr(clipboard, "_read_x11", lambda: list(paths))
        monkeypatch.setattr(clipboard, "_read_windows", lambda: list(paths))

    return put


def gestures(node):
    seen = []
    node.core.events.subscribe(lambda n, d: seen.append(d["gesture"]) if n == "cv.event" else None)
    return seen


def test_grab_then_release_delivers_the_copied_file_to_the_connected_device(
    pair, tmp_path, on_clipboard
):
    a, b = pair
    connect_pair(a, b)
    data = os.urandom(50_000)
    f = tmp_path / "photo.png"
    f.write_bytes(data)
    on_clipboard([str(f)])
    seen = gestures(a)

    hand = a.core.hand_control
    hand.on_grab()
    assert seen == ["copied"]
    hand.on_release()

    wait_for(lambda: b.received_files() == {"photo.png": data}, what="the file to arrive")
    assert seen == ["copied", "sent"]
    assert f.read_bytes() == data  # the original is untouched


def test_release_with_no_connected_device_is_rejected_and_nothing_is_queued(
    pair, tmp_path, on_clipboard
):
    a, b = pair  # never connected
    f = tmp_path / "a.txt"
    f.write_bytes(b"x")
    on_clipboard([str(f)])
    seen = gestures(a)
    a.core.hand_control.on_grab()
    a.core.hand_control.on_release()
    assert seen == ["copied", "send_failed"]
    assert a.ok("history.list")["items"] == []
    a.core.hand_control.on_release()  # not retried later
    assert a.ok("history.list")["items"] == []


@pytest.mark.parametrize("name", ["setup.exe", "movie.mp4", "notes.docx"])
def test_disallowed_types_on_the_clipboard_are_refused_all_or_nothing(
    pair, tmp_path, on_clipboard, name
):
    a, b = pair
    connect_pair(a, b)
    ok, bad = tmp_path / "ok.txt", tmp_path / name
    ok.write_bytes(b"fine")
    bad.write_bytes(b"MZ....")
    on_clipboard([str(ok), str(bad)])
    seen = gestures(a)
    a.core.hand_control.on_grab()
    a.core.hand_control.on_release()
    assert seen == ["copied", "send_failed"]
    assert b.received_files() == {}


def test_an_executable_renamed_to_an_allowed_extension_is_refused(pair, tmp_path, on_clipboard):
    a, b = pair
    connect_pair(a, b)
    f = tmp_path / "invoice.pdf"
    f.write_bytes(b"MZ" + os.urandom(100))
    on_clipboard([str(f)])
    seen = gestures(a)
    a.core.hand_control.on_grab()
    a.core.hand_control.on_release()
    assert seen == ["copied", "send_failed"] and b.received_files() == {}

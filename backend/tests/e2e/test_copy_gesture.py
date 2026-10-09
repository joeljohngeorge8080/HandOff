"""COPY gesture end to end (ADR-057, ADR-058): grab on laptop A, release on laptop B."""

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


def make(tmp_path, name, data=b"hello"):
    f = tmp_path / name
    f.write_bytes(data)
    return f


def test_grab_on_one_laptop_and_release_on_the_other_delivers_the_file(
    pair, tmp_path, on_clipboard
):
    a, b = pair
    connect_pair(a, b)
    data = os.urandom(50_000)
    f = make(tmp_path, "photo.png", data)
    on_clipboard([str(f)])
    a_seen, b_seen = gestures(a), gestures(b)

    a.core.hand_control.on_grab()  # laptop A: open palm -> closed palm
    assert a_seen == ["copied"]
    b.core.hand_control.on_release()  # laptop B: the closed hand arrives and opens

    wait_for(lambda: b.received_files() == {"photo.png": data}, what="the file to arrive on B")
    assert a_seen == ["copied", "sent"] and b_seen == ["claimed"]
    assert f.read_bytes() == data  # the original is untouched


def test_opening_the_hand_on_the_grabbing_laptop_sends_nothing_and_cancels_the_grab(
    pair, tmp_path, on_clipboard
):
    a, b = pair
    connect_pair(a, b)
    on_clipboard([str(make(tmp_path, "a.txt"))])
    a_seen, b_seen = gestures(a), gestures(b)
    a.core.hand_control.on_grab()
    a.core.hand_control.on_release()  # same laptop: cancel
    assert a_seen == ["copied", "copy_cancelled"]
    assert a.ok("history.list")["items"] == [] and b.received_files() == {}
    b.core.hand_control.on_release()  # nothing is held any more
    assert b_seen == ["claim_failed"] and b.received_files() == {}


def test_a_release_when_the_other_laptop_holds_nothing_is_refused(pair):
    a, b = pair
    connect_pair(a, b)
    b_seen = gestures(b)
    b.core.hand_control.on_release()
    assert b_seen == ["claim_failed"] and a.ok("history.list")["items"] == []


def test_a_release_with_no_connected_laptop_is_refused_and_nothing_is_queued(
    pair, tmp_path, on_clipboard
):
    a, b = pair  # never connected
    on_clipboard([str(make(tmp_path, "a.txt"))])
    a.core.hand_control.on_grab()
    b_seen = gestures(b)
    b.core.hand_control.on_release()
    assert b_seen == ["claim_failed"] and a.ok("history.list")["items"] == []


def test_one_grab_sends_once_even_if_the_other_laptop_releases_twice(pair, tmp_path, on_clipboard):
    a, b = pair
    connect_pair(a, b)
    on_clipboard([str(make(tmp_path, "once.txt", b"x"))])
    a.core.hand_control.on_grab()
    b.core.hand_control.on_release()
    wait_for(lambda: b.received_files() == {"once.txt": b"x"}, what="the file to arrive")
    b_seen = gestures(b)
    b.core.hand_control.on_release()
    assert b_seen == ["claim_failed"]
    assert len(a.ok("history.list")["items"]) == 1


@pytest.mark.parametrize("name", ["setup.exe", "movie.mp4", "notes.docx"])
def test_disallowed_types_are_refused_all_or_nothing_and_never_reach_the_other_laptop(
    pair, tmp_path, on_clipboard, name
):
    a, b = pair
    connect_pair(a, b)
    ok, bad = make(tmp_path, "ok.txt", b"fine"), make(tmp_path, name, b"MZ....")
    on_clipboard([str(ok), str(bad)])
    a_seen, b_seen = gestures(a), gestures(b)
    a.core.hand_control.on_grab()
    b.core.hand_control.on_release()
    assert a_seen == ["copied", "send_failed"] and b_seen == ["claim_failed"]
    assert b.received_files() == {}


def test_an_executable_renamed_to_an_allowed_extension_is_refused(pair, tmp_path, on_clipboard):
    a, b = pair
    connect_pair(a, b)
    on_clipboard([str(make(tmp_path, "invoice.pdf", b"MZ" + os.urandom(100)))])
    a_seen, b_seen = gestures(a), gestures(b)
    a.core.hand_control.on_grab()
    b.core.hand_control.on_release()
    assert a_seen == ["copied", "send_failed"] and b_seen == ["claim_failed"]
    assert b.received_files() == {}

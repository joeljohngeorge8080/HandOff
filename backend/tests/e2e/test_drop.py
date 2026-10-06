"""Edge drop pipeline (ADR-054): drop.inspect / drop.send, cleanup and push events."""

import os
import sys
from pathlib import Path

import pytest
from helpers import connect_pair, pause_upload, wait_for

MB = 1024 * 1024


@pytest.fixture
def pair(node_factory):
    return node_factory("Alice"), node_factory("Bob")


@pytest.fixture
def src(tmp_path):
    d = tmp_path / "Desktop-files"
    d.mkdir()

    def make(name: str, data: bytes = b"hello") -> str:
        p = d / name
        p.write_bytes(data)
        return str(p)

    return make


def managed_files(node):
    return list(node.paths.files_dir.iterdir())


def audit_events(node):
    from handoff.db.repositories import AuditRepository

    with node.core.db.session() as s:
        return [r.event_type for r in AuditRepository(s).list(limit=1000)]


def drop(a, paths):
    return a.ok("drop.send", {"paths": paths})["transfer"]["transfer_id"]


# ---------------------------------------------------------------- drop.inspect


def test_inspect_accepts_supported_files_case_insensitively(pair, src):
    a, _ = pair
    out = a.ok(
        "drop.inspect",
        {"paths": [src("a.TXT"), src("b.JPG", b"xy"), src("c.pdf"), src("d.png"), src("e.jpeg")]},
    )
    assert out["ok"] is True and out["file_count"] == 5 and out["total_size"] == 5 + 2 + 5 * 3
    assert [i["ok"] for i in out["items"]] == [True] * 5
    assert [i["name"] for i in out["items"]][:2] == ["a.TXT", "b.JPG"]


def test_inspect_flags_each_bad_item_with_a_reason(pair, src, tmp_path):
    a, _ = pair
    folder = tmp_path / "Photos"
    folder.mkdir()
    out = a.ok(
        "drop.inspect",
        {"paths": [src("ok.png"), src("setup.exe", b"x"), str(folder), str(tmp_path / "gone.txt")]},
    )
    assert out["ok"] is False
    by = {i["name"]: i for i in out["items"]}
    assert by["ok.png"]["ok"] is True
    assert (
        by["setup.exe"]["code"] == "FILE_TYPE_NOT_SUPPORTED"
        and by["setup.exe"]["reason"] == "unsupported_type"
    )
    assert by["Photos"]["code"] == "INVALID_FILE" and by["Photos"]["reason"] == "directory"
    assert by["gone.txt"]["code"] == "FILE_NOT_FOUND" and by["gone.txt"]["reason"] == "missing"


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_inspect_flags_symlinks_and_shortcuts(pair, src, tmp_path):
    a, _ = pair
    link = tmp_path / "link.txt"
    link.symlink_to(src("real.txt"))
    out = a.ok("drop.inspect", {"paths": [str(link), src("Game.lnk"), src("App.desktop")]})
    assert [i["reason"] for i in out["items"]] == [
        "symlink",
        "unsupported_type",
        "unsupported_type",
    ]


def test_inspect_flags_a_renamed_executable(pair, src):
    a, _ = pair
    out = a.ok("drop.inspect", {"paths": [src("photo.jpg", b"MZ\x90\x00" + os.urandom(50))]})
    (item,) = out["items"]
    assert item["ok"] is False and item["reason"] == "executable_content"


def test_inspect_flags_a_file_over_50_mb(pair, tmp_path):
    a, _ = pair
    big = tmp_path / "big.png"
    with big.open("wb") as f:
        f.truncate(50 * MB + 1)
    (item,) = a.ok("drop.inspect", {"paths": [str(big)]})["items"]
    assert item["code"] == "FILE_TOO_LARGE" and item["reason"] == "too_large"


def test_inspect_copies_and_records_nothing(pair, src):
    a, _ = pair
    a.ok("drop.inspect", {"paths": [src("a.txt"), src("b.exe")]})
    assert managed_files(a) == [] and a.ok("files.list")["files"] == []
    assert "FILE_IMPORTED" not in audit_events(a)


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        {"paths": []},
        {"paths": "a.txt"},
        {"paths": [1]},
        {"paths": [""]},
        {"paths": ["a\x00b.txt"]},
        {"paths": ["/x.txt"] * 101},
    ],
)
def test_inspect_validates_its_payload(pair, payload):
    a, _ = pair
    assert a.rpc("drop.inspect", payload)["error"]["code"] == "INVALID_REQUEST"


# ---------------------------------------------------------------- drop.send: refusals


def test_dropping_with_no_connected_device_is_rejected_and_nothing_is_kept(pair, src):
    a, _ = pair
    out = a.rpc("drop.send", {"paths": [src("a.txt")]})
    assert out["error"]["code"] == "DEVICE_NOT_FOUND"
    assert out["error"]["message"] == "No HandOff device connected"
    assert managed_files(a) == [] and a.ok("history.list")["items"] == []  # not queued


def test_dropping_on_an_offline_peer_is_rejected(pair, src):
    a, b = pair
    connect_pair(a, b)
    b.stop()
    wait_for(
        lambda: a.ok("devices.status")["connection"]["device"]["status"] == "offline",
        what="offline",
    )
    out = a.rpc("drop.send", {"paths": [src("a.txt")]})
    assert out["error"]["code"] == "DEVICE_OFFLINE"
    assert managed_files(a) == [] and a.ok("history.list")["items"] == []


def test_a_bad_item_rejects_the_whole_drop_before_anything_is_copied(pair, src):
    a, b = pair
    connect_pair(a, b)
    out = a.rpc("drop.send", {"paths": [src("good.txt"), src("bad.exe", b"x"), src("good.png")]})
    err = out["error"]
    assert err["code"] == "FILE_TYPE_NOT_SUPPORTED"
    assert [i["ok"] for i in err["details"]["items"]] == [True, False, True]
    assert managed_files(a) == [] and a.ok("history.list")["items"] == []
    assert b.received_files() == {}
    assert "UNSUPPORTED_FILE" in audit_events(a)  # security-relevant rejection is logged
    assert "TRANSFER_CREATED" not in audit_events(a)


def test_a_folder_in_the_drop_rejects_the_whole_drop(pair, src, tmp_path):
    a, b = pair
    connect_pair(a, b)
    folder = tmp_path / "Album"
    folder.mkdir()
    out = a.rpc("drop.send", {"paths": [src("good.txt"), str(folder)]})
    assert out["error"]["code"] == "INVALID_FILE"
    assert out["error"]["details"]["items"][1]["reason"] == "directory"
    assert managed_files(a) == [] and a.ok("history.list")["items"] == []


def test_dropping_during_an_active_transfer_is_rejected_without_copying(
    pair, src, monkeypatch, gate
):
    a, b = pair
    connect_pair(a, b)
    pause_upload(monkeypatch, gate)
    tid = drop(a, [src("one.png", os.urandom(100_000))])
    assert gate.started.wait(10)
    before = managed_files(a)
    out = a.rpc("drop.send", {"paths": [src("two.png")]})
    assert out["error"]["code"] == "INVALID_STATE"
    assert managed_files(a) == before  # nothing extra was imported
    gate.release.set()
    assert a.wait_terminal(tid)["status"] == "completed"


def test_dropping_while_receiving_is_also_rejected(pair, src, monkeypatch, gate):
    a, b = pair
    connect_pair(a, b)
    pause_upload(monkeypatch, gate)
    drop(a, [src("one.png", os.urandom(100_000))])
    assert gate.started.wait(10)
    wait_for(lambda: b.ok("status.snapshot")["active_transfer"] is not None, what="receiving")
    out = b.rpc("drop.send", {"paths": [src("back.txt")]})  # B drops back to A (its peer)
    assert out["error"]["code"] == "INVALID_STATE"
    gate.release.set()


# ---------------------------------------------------------------- drop.send: success


def test_a_single_drop_arrives_and_the_managed_copy_is_deleted(pair, src):
    a, b = pair
    connect_pair(a, b)
    data = os.urandom(200_000)
    original = src("photo.jpg", data)
    tid = drop(a, [original])
    assert a.wait_terminal(tid)["status"] == "completed"
    assert b.received_files() == {"photo.jpg": data}
    wait_for(lambda: managed_files(a) == [], what="managed copy to be deleted")
    assert a.ok("files.list")["files"] == []
    assert Path(original).read_bytes() == data  # the user's original is untouched
    assert "FILE_DELETED" in audit_events(a)
    assert a.ok("history.list")["items"][0]["transfer_id"] == tid  # history survives deletion


def test_several_files_become_one_transfer_job(pair, src):
    a, b = pair
    connect_pair(a, b)
    files = {"a.txt": b"text", "b.png": os.urandom(5000), "c.PDF": os.urandom(7000), "d.jpeg": b"j"}
    tid = drop(a, [src(n, d) for n, d in files.items()])
    assert a.wait_terminal(tid)["status"] == "completed"
    assert b.received_files() == files
    assert len(a.ok("history.list")["items"]) == 1 and b.transfer(tid)["file_count"] == 4
    wait_for(lambda: managed_files(a) == [], what="cleanup")


def test_repeated_drops_all_work_and_never_overwrite(pair, src):
    a, b = pair
    connect_pair(a, b)
    for _ in range(3):
        assert a.wait_terminal(drop(a, [src("photo.jpg", b"same")]))["status"] == "completed"
    assert sorted(b.received_files()) == ["photo(1).jpg", "photo(2).jpg", "photo.jpg"]
    wait_for(lambda: managed_files(a) == [], what="cleanup")


def test_a_second_drop_while_the_first_name_is_still_managed_gets_a_unique_name(pair, src):
    a, b = pair
    connect_pair(a, b)
    p = src("same.txt", b"v1")
    assert a.wait_terminal(drop(a, [p]))["status"] == "completed"
    assert a.wait_terminal(drop(a, [p]))["status"] == "completed"
    assert sorted(b.received_files()) == ["same(1).txt", "same.txt"]


# ---------------------------------------------------------------- drop.send: failures


def test_a_failed_transfer_still_deletes_the_managed_copy(pair, src):
    a, b = pair
    connect_pair(a, b)
    b.inbox.rmdir()  # the receiver cannot take files
    tid = drop(a, [src("photo.jpg")])
    done = a.wait_terminal(tid)
    assert done["status"] == "failed" and done["error_code"] == "RECEIVER_NOT_READY"
    wait_for(lambda: managed_files(a) == [], what="cleanup after failure")
    # and the edge is usable again: fix the folder, drop again
    b.inbox.mkdir()
    assert a.wait_terminal(drop(a, [src("photo.jpg")]))["status"] == "completed"


def test_a_receiver_that_disappears_mid_transfer_fails_it_and_cleans_up(
    pair, src, monkeypatch, gate
):
    a, b = pair
    connect_pair(a, b)
    pause_upload(monkeypatch, gate)
    tid = drop(a, [src("clip.png", os.urandom(200_000))])
    assert gate.started.wait(10)
    b.stop()
    gate.release.set()
    assert a.wait_terminal(tid, timeout=30)["status"] == "failed"
    wait_for(lambda: managed_files(a) == [], what="cleanup after receiver vanished")
    assert a.ok("status.snapshot")["active_transfer"] is None


def test_a_corrupted_managed_copy_is_reported_and_still_cleaned_up(pair, src):
    a, b = pair
    connect_pair(a, b)
    original = src("photo.jpg", b"A" * 3000)
    # corrupt the managed copy after import but before upload by tampering as soon as it exists
    import handoff.transfer.sender as sender_mod

    real = sender_mod.SenderService._prepare

    def tamper(self, tid, ids):
        entries, manifest = real(self, tid, ids)
        entries[0][0].write_bytes(b"B" * 3000)  # same size, different bytes
        return entries, manifest

    sender_mod.SenderService._prepare = tamper
    try:
        done = a.wait_terminal(drop(a, [original]))
    finally:
        sender_mod.SenderService._prepare = real
    assert done["status"] == "failed" and done["files"][0]["failure_code"] == "INVALID_HASH"
    assert b.received_files() == {}
    wait_for(lambda: managed_files(a) == [], what="cleanup")


def test_if_creating_the_transfer_fails_the_imports_are_rolled_back(pair, src, monkeypatch):
    a, b = pair
    connect_pair(a, b)
    from handoff.errors import HandOffError

    def boom(*args, **kwargs):
        raise HandOffError("DEVICE_OFFLINE", "Bob is offline.")

    monkeypatch.setattr(a.network.sender, "create", boom)
    out = a.rpc("drop.send", {"paths": [src("a.txt"), src("b.png")]})
    assert out["error"]["code"] == "DEVICE_OFFLINE"
    assert managed_files(a) == [] and a.ok("files.list")["files"] == []


def test_a_file_that_vanishes_between_inspect_and_send_leaves_nothing_behind(pair, src, tmp_path):
    a, b = pair
    connect_pair(a, b)
    keep, gone = src("keep.txt"), src("gone.txt")
    os.remove(gone)
    out = a.rpc("drop.send", {"paths": [keep, gone]})
    assert out["error"]["code"] == "FILE_NOT_FOUND"
    assert managed_files(a) == []


# ---------------------------------------------------------------- crash safety


def test_managed_copies_left_by_a_crash_are_swept_at_startup(pair, src):
    a, _ = pair
    original = src("left-over.txt", b"important original")
    a.ok("files.add", {"paths": [original]})  # imported, then the app "crashes"
    assert len(managed_files(a)) == 1
    a.restart()
    assert managed_files(a) == [] and a.ok("files.list")["files"] == []
    assert Path(original).read_bytes() == b"important original"
    assert "FILE_DELETED" in audit_events(a)


# ---------------------------------------------------------------- push events


class Sink:
    def __init__(self):
        self.events = []

    def __call__(self, name, data):
        self.events.append((name, data))

    def transfers(self, direction=None):
        return [
            d
            for n, d in self.events
            if n == "transfer.updated" and (direction is None or d["direction"] == direction)
        ]


def test_the_sender_pushes_every_state_and_ends_on_the_backends_verdict(pair, src):
    a, b = pair
    sink = Sink()
    a.core.events.subscribe(sink)
    connect_pair(a, b)
    tid = drop(a, [src("photo.jpg", os.urandom(300_000))])
    a.wait_terminal(tid)
    wait_for(
        lambda: sink.transfers() and sink.transfers()[-1]["status"] == "completed", what="event"
    )
    statuses = [t["status"] for t in sink.transfers()]
    order = ["created", "validating", "accepted", "transferring", "completed"]
    assert [s for s in order if s in statuses] == order
    assert statuses == sorted(statuses, key=order.index)  # never goes backwards
    assert statuses.count("completed") >= 1 and statuses[-1] == "completed"
    assert all(t["direction"] == "sent" and t["transfer_id"] == tid for t in sink.transfers())
    first = sink.transfers()[0]
    assert first["peer_device_name"] == "Bob" and first["files"][0]["name"] == "photo.jpg"


def test_the_receiver_pushes_progress_and_its_own_verdict(pair, src, monkeypatch, gate):
    a, b = pair
    sink = Sink()
    b.core.events.subscribe(sink)
    connect_pair(a, b)
    pause_upload(monkeypatch, gate)
    tid = drop(a, [src("clip.png", os.urandom(200_000))])
    assert gate.started.wait(10)
    wait_for(
        lambda: any(t["status"] == "transferring" for t in sink.transfers("received")), what="rx"
    )
    gate.release.set()
    a.wait_terminal(tid)
    wait_for(lambda: sink.transfers("received")[-1]["status"] == "completed", what="rx done")
    assert all(t["direction"] == "received" for t in sink.transfers())
    assert sink.transfers("received")[-1]["peer_device_name"] == "Alice"


def test_failures_are_pushed_as_failed_never_as_completed(pair, src):
    a, b = pair
    sink = Sink()
    a.core.events.subscribe(sink)
    connect_pair(a, b)
    b.inbox.rmdir()
    a.wait_terminal(drop(a, [src("photo.jpg")]))
    wait_for(lambda: sink.transfers()[-1]["status"] == "failed", what="failed event")
    assert "completed" not in [t["status"] for t in sink.transfers()]
    assert sink.transfers()[-1]["error_code"] == "RECEIVER_NOT_READY"


def test_connection_changes_are_pushed(pair):
    a, b = pair
    sink = Sink()
    a.core.events.subscribe(sink)
    connect_pair(a, b)
    changes = [d for n, d in sink.events if n == "connection.changed"]
    assert (
        changes
        and changes[-1]["connected"] is True
        and changes[-1]["device"]["device_name"] == "Bob"
    )


def test_a_failing_event_listener_never_breaks_a_transfer(pair, src):
    a, b = pair

    def broken(name, data):
        raise RuntimeError("UI went away")

    a.core.events.subscribe(broken)
    b.core.events.subscribe(broken)
    connect_pair(a, b)
    assert a.wait_terminal(drop(a, [src("photo.jpg")]))["status"] == "completed"
    assert list(b.received_files()) == ["photo.jpg"]

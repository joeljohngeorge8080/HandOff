"""End-to-end: full nodes talking real HTTPS on loopback, driven through the IPC actions."""

import hashlib
import os

import pytest
from helpers import connect_pair, wait_for

import handoff.transfer.sender as sender_mod

MB = 1024 * 1024


@pytest.fixture
def pair(node_factory):
    a, b = node_factory("Alice"), node_factory("Bob")
    return a, b


@pytest.fixture
def trio(node_factory):
    return node_factory("Alice"), node_factory("Bob"), node_factory("Carol")


def send(a, b, ids):
    return a.ok("transfer.create", {"file_ids": ids, "destination_device_id": b.device_id})[
        "transfer"
    ]["transfer_id"]


def received(node):
    return {f["name"]: f for f in node.ok("files.list")["files"] if f["source"] == "received"}


def stored_bytes(node, file_id):
    return node.core.files.file_path(file_id).read_bytes()


def pause_upload(monkeypatch, gate, *, then_raise=None):
    """Hold the sender's upload after its first chunk until the test releases the gate."""
    monkeypatch.setattr(sender_mod, "IO_CHUNK_SIZE", 1024)
    orig = sender_mod.SenderService._chunks

    def slow(self, path, tid):
        for i, chunk in enumerate(orig(self, path, tid)):
            yield chunk
            if i == 0:
                gate.started.set()
                assert gate.release.wait(30)
                if then_raise:
                    raise then_raise

    monkeypatch.setattr(sender_mod.SenderService, "_chunks", slow)


def _audit_rows(node):
    from handoff.db.repositories import AuditRepository

    with node.core.db.session() as s:
        return AuditRepository(s).list(limit=1000)


def audit_set(node):
    return {r.event_type for r in _audit_rows(node)}


# ------------------------------------------------------------------ discovery and connection


def test_discover_connect_and_mutual_trust(pair):
    a, b = pair
    a.see(b)
    b.see(a)
    (found,) = a.ok("devices.discover")["devices"]
    assert found["device_id"] == b.device_id and found["device_name"] == "Bob"
    assert found["status"] == "available" and found["is_trusted"] is False

    conn = a.ok("devices.connect", {"device_id": b.device_id})["connection"]
    assert conn["connected"] is True and conn["device"]["device_name"] == "Bob"

    # both sides now consider each other connected and trusted
    assert b.ok("devices.status")["connection"]["device"]["device_id"] == a.device_id
    for me, other in ((a, b), (b, a)):
        (known,) = me.ok("devices.list")["devices"]
        assert known["device_id"] == other.device_id and known["is_trusted"] is True
        assert "DEVICE_CONNECTED" in audit_set(me)
    assert a.ok("devices.discover")["devices"][0]["status"] == "connected"
    assert a.ok("status.snapshot")["connection"]["connected"] is True


def test_connecting_twice_is_harmless(pair):
    a, b = pair
    connect_pair(a, b)
    assert a.ok("devices.connect", {"device_id": b.device_id})["connection"]["connected"] is True
    assert len(a.ok("devices.list")["devices"]) == 1


def test_connecting_to_a_device_that_is_not_visible_fails(pair):
    a, _ = pair
    out = a.rpc("devices.connect", {"device_id": "00000000-0000-4000-8000-000000000000"})
    assert out["error"]["code"] == "DEVICE_NOT_FOUND"


def test_identity_mismatch_on_connect_is_refused_and_nothing_is_trusted(trio):
    a, b, c = trio
    # an attacker advertises Bob's name and device ID with Carol's key
    spoof = b.network.advertisement()
    from dataclasses import replace

    a.discovery.set_peers([replace(spoof, fingerprint=c.core.identity.public_key_b64)])
    out = a.rpc("devices.connect", {"device_id": b.device_id})
    assert out["error"]["code"] == "PEER_CONNECTION_FAILED"
    assert a.ok("devices.status")["connection"] == {"connected": False, "device": None}
    assert "TLS_FAILURE" in audit_set(a)
    assert b.ok("devices.list")["devices"] == []  # Bob never learned about, or trusted, Alice
    assert a.ok("devices.list")["devices"] == []


def test_an_unconnected_device_cannot_send_to_a_receiver(trio):
    from helpers import transfer_body

    from handoff.devices.client import PeerClient

    _, b, c = trio
    b.ok("receive_mode.set", {"enabled": True})
    body = transfer_body("tr_x", {"a.txt": b"x"}, source=c.device_id)
    r = PeerClient(c.core.identity).request(
        "127.0.0.1", b.network.port, "POST", "/api/v1/transfers",
        expected_key=b.core.identity.public_key_b64, json=body,
    )  # fmt: skip
    assert r.status_code == 403
    assert "INVALID_DEVICE" in audit_set(b)
    assert b.ok("history.list")["items"] == []


# ------------------------------------------------------------------ successful transfers


def test_single_file_transfer_end_to_end(pair, tmp_path):
    a, b = pair
    connect_pair(a, b)
    b.ok("receive_mode.set", {"enabled": True})
    data = os.urandom(300_000)
    fid = a.import_bytes("photo.jpg", data, tmp_path)

    tid = send(a, b, [fid])
    done = a.wait_terminal(tid)
    assert done["status"] == "completed"
    assert done["files"] == [
        {"name": "photo.jpg", "size": len(data), "status": "completed", "failure_code": None}
    ]
    assert done["bytes_transferred"] == done["archive_size"] > len(data)

    # receiver: file present, identical, hash verified, history recorded
    wait_for(lambda: b.transfer(tid)["status"] == "completed", what="receiver to finish")
    (rec,) = received(b).values()
    assert stored_bytes(b, rec["id"]) == data
    with b.core.db.session() as s:
        from handoff.db.models import File

        assert s.get(File, rec["id"]).sha256 == hashlib.sha256(data).hexdigest()
    hist_b = b.transfer(tid)
    assert (hist_b["direction"], hist_b["peer_device_name"]) == ("received", "Alice")
    assert (done["direction"], done["peer_device_name"]) == ("sent", "Bob")

    # copy semantics: the sender still has its file
    assert stored_bytes(a, fid) == data
    assert [f["name"] for f in a.ok("files.list")["files"]] == ["photo.jpg"]

    # no leftovers, audit trail on both sides
    for n in (a, b):
        assert (
            list(n.paths.temp_dir.iterdir()) == [] and list(n.paths.transfers_dir.iterdir()) == []
        )
    assert {"TRANSFER_CREATED", "TRANSFER_STARTED", "TRANSFER_COMPLETED"} <= audit_set(a)
    assert {"TRANSFER_CREATED", "FILE_RECEIVED", "TRANSFER_COMPLETED"} <= audit_set(b)


def test_multiple_files_travel_as_one_transfer_job(pair, tmp_path):
    a, b = pair
    connect_pair(a, b)
    b.ok("receive_mode.set", {"enabled": True})
    contents = {
        "notes.txt": b"hello world",
        "photo.jpg": os.urandom(50_000),
        "setup.exe": os.urandom(200_000),
        "clip.mp4": os.urandom(2 * MB),
    }
    ids = [a.import_bytes(n, d, tmp_path) for n, d in contents.items()]
    tid = send(a, b, ids)
    assert a.wait_terminal(tid)["status"] == "completed"
    wait_for(lambda: len(received(b)) == 4, what="all files")
    for name, data in contents.items():
        assert stored_bytes(b, received(b)[name]["id"]) == data
    assert len(a.ok("history.list")["items"]) == 1 and len(b.ok("history.list")["items"]) == 1
    assert b.transfer(tid)["file_count"] == 4


def test_sending_the_same_file_twice_never_overwrites(pair, tmp_path):
    a, b = pair
    connect_pair(a, b)
    b.ok("receive_mode.set", {"enabled": True})
    fid = a.import_bytes("photo.jpg", b"same bytes", tmp_path)
    for _ in range(2):
        assert a.wait_terminal(send(a, b, [fid]))["status"] == "completed"
    assert sorted(received(b)) == ["photo(1).jpg", "photo.jpg"]


def test_the_ui_can_watch_progress_of_a_running_transfer(pair, tmp_path, monkeypatch, gate):
    a, b = pair
    connect_pair(a, b)
    b.ok("receive_mode.set", {"enabled": True})
    pause_upload(monkeypatch, gate)
    fid = a.import_bytes("big.mp4", os.urandom(200_000), tmp_path)
    tid = send(a, b, [fid])
    assert gate.started.wait(10)
    active = a.ok("status.snapshot")["active_transfer"]
    assert active["transfer_id"] == tid and active["status"] == "transferring"
    assert active["archive_size"] > 0
    wait_for(lambda: b.ok("status.snapshot")["active_transfer"] is not None, what="receiver active")
    gate.release.set()
    assert a.wait_terminal(tid)["status"] == "completed"
    assert a.ok("status.snapshot")["active_transfer"] is None


# ------------------------------------------------------------------ refusals and failures


def test_receive_mode_off_blocks_the_transfer_before_anything_is_sent(pair, tmp_path):
    a, b = pair
    connect_pair(a, b)
    fid = a.import_bytes("a.txt", b"x", tmp_path)
    out = a.rpc("transfer.create", {"file_ids": [fid], "destination_device_id": b.device_id})
    assert out["error"]["code"] == "RECEIVE_MODE_DISABLED"
    assert "Bob is not accepting files" in out["error"]["message"]
    assert a.ok("history.list")["items"] == [] and b.ok("history.list")["items"] == []
    assert "TRANSFER_REJECTED" in audit_set(a)


def test_receive_mode_switched_off_after_the_check_fails_the_transfer_cleanly(
    pair, tmp_path, monkeypatch
):
    a, b = pair
    connect_pair(a, b)
    b.ok("receive_mode.set", {"enabled": True})
    monkeypatch.setattr(a.network.sender, "_require_receiver_ready", lambda peer: None)
    b.ok("receive_mode.set", {"enabled": False})  # the race: it was ON when we checked
    fid = a.import_bytes("a.txt", b"x", tmp_path)
    done = a.wait_terminal(send(a, b, [fid]))
    assert done["status"] == "failed" and done["error_code"] == "RECEIVE_MODE_DISABLED"
    assert done["files"][0]["status"] == "failed"
    assert b.ok("history.list")["items"] == [] and received(b) == {}
    assert "TRANSFER_REJECTED" in audit_set(b)


def test_a_corrupted_file_gives_a_partially_completed_transfer_on_both_sides(pair, tmp_path):
    a, b = pair
    connect_pair(a, b)
    b.ok("receive_mode.set", {"enabled": True})
    good1 = a.import_bytes("one.txt", b"first file", tmp_path)
    bad = a.import_bytes("two.jpg", b"B" * 5000, tmp_path)
    good2 = a.import_bytes("three.exe", b"third file", tmp_path)
    a.core.files.file_path(bad).write_bytes(b"X" * 5000)  # same size, bytes rot on the sender

    tid = send(a, b, [good1, bad, good2])
    done = a.wait_terminal(tid)
    assert done["status"] == "partially_completed"
    assert {f["name"]: f["status"] for f in done["files"]} == {
        "one.txt": "completed", "two.jpg": "failed", "three.exe": "completed",
    }  # fmt: skip
    assert [f["failure_code"] for f in done["files"] if f["status"] == "failed"] == ["INVALID_HASH"]
    wait_for(lambda: b.transfer(tid)["status"] == "partially_completed", what="receiver")
    assert sorted(received(b)) == ["one.txt", "three.exe"]
    assert "INVALID_HASH" in audit_set(b)
    assert "TRANSFER_PARTIALLY_COMPLETED" in audit_set(a)


def test_when_every_file_is_corrupted_the_transfer_fails(pair, tmp_path):
    a, b = pair
    connect_pair(a, b)
    b.ok("receive_mode.set", {"enabled": True})
    fid = a.import_bytes("only.txt", b"original", tmp_path)
    a.core.files.file_path(fid).write_bytes(b"tampered")  # same length
    done = a.wait_terminal(send(a, b, [fid]))
    assert done["status"] == "failed" and done["files"][0]["status"] == "failed"
    assert received(b) == {}


def test_only_one_transfer_can_be_active_at_a_time(pair, tmp_path, monkeypatch, gate):
    a, b = pair
    connect_pair(a, b)
    b.ok("receive_mode.set", {"enabled": True})
    pause_upload(monkeypatch, gate)
    f1 = a.import_bytes("one.mp4", os.urandom(100_000), tmp_path)
    f2 = a.import_bytes("two.mp4", b"small", tmp_path)
    tid = send(a, b, [f1])
    assert gate.started.wait(10)
    out = a.rpc("transfer.create", {"file_ids": [f2], "destination_device_id": b.device_id})
    assert out["error"]["code"] == "INVALID_STATE"
    gate.release.set()
    assert a.wait_terminal(tid)["status"] == "completed"
    assert a.wait_terminal(send(a, b, [f2]))["status"] == "completed"  # fine once the first ended


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        ({"file_ids": [], "destination_device_id": "x"}, "INVALID_REQUEST"),
        ({"file_ids": "a", "destination_device_id": "x"}, "INVALID_REQUEST"),
        ({"file_ids": ["a", "a"], "destination_device_id": "x"}, "INVALID_REQUEST"),
        ({"file_ids": ["a"], "destination_device_id": 5}, "INVALID_REQUEST"),
        ({"file_ids": ["a"]}, "INVALID_REQUEST"),
        ({"file_ids": ["a"], "destination_device_id": "not-connected"}, "DEVICE_NOT_FOUND"),
    ],
)
def test_transfer_create_validates_its_input(pair, payload, code):
    a, b = pair
    connect_pair(a, b)
    assert a.rpc("transfer.create", payload)["error"]["code"] == code
    assert a.ok("history.list")["items"] == []


def test_unknown_files_cannot_be_sent(pair):
    a, b = pair
    connect_pair(a, b)
    b.ok("receive_mode.set", {"enabled": True})
    out = a.rpc("transfer.create", {"file_ids": ["nope"], "destination_device_id": b.device_id})
    assert out["error"]["code"] == "FILE_NOT_FOUND"


def test_a_file_that_changed_size_on_disk_is_not_sent(pair, tmp_path):
    a, b = pair
    connect_pair(a, b)
    b.ok("receive_mode.set", {"enabled": True})
    fid = a.import_bytes("a.txt", b"12345", tmp_path)
    a.core.files.file_path(fid).write_bytes(b"123")
    out = a.rpc("transfer.create", {"file_ids": [fid], "destination_device_id": b.device_id})
    assert out["error"]["code"] == "FILE_STORAGE_ERROR"


# ------------------------------------------------------------------ switching peers


def test_switching_peers_is_blocked_during_a_transfer_then_allowed(
    trio, tmp_path, monkeypatch, gate
):
    a, b, c = trio
    connect_pair(a, b)
    b.ok("receive_mode.set", {"enabled": True})
    a.see(b, c)
    c.see(a)
    pause_upload(monkeypatch, gate)
    fid = a.import_bytes("clip.mp4", os.urandom(100_000), tmp_path)
    tid = send(a, b, [fid])
    assert gate.started.wait(10)

    out = a.rpc("devices.connect", {"device_id": c.device_id})
    assert out["error"]["code"] == "INVALID_STATE"
    assert a.ok("devices.status")["connection"]["device"]["device_id"] == b.device_id  # unchanged

    gate.release.set()
    assert a.wait_terminal(tid)["status"] == "completed"

    now = a.ok("devices.connect", {"device_id": c.device_id})["connection"]
    assert now["device"]["device_id"] == c.device_id and now["connected"] is True
    # Bob was told (graceful internal disconnect) but still trusts Alice
    assert b.ok("devices.status")["connection"]["connected"] is False
    assert any(d["is_trusted"] for d in b.ok("devices.list")["devices"])
    assert c.ok("devices.status")["connection"]["device"]["device_id"] == a.device_id
    # and the old peer is no longer a valid destination
    out = a.rpc("transfer.create", {"file_ids": [fid], "destination_device_id": b.device_id})
    assert out["error"]["code"] == "DEVICE_NOT_FOUND"


def test_a_receiver_connected_to_someone_else_turns_away_a_second_device(trio):
    a, b, c = trio
    connect_pair(a, b)
    c.see(b)
    out = c.rpc("devices.connect", {"device_id": b.device_id})
    assert out["error"]["code"] == "DEVICE_ALREADY_CONNECTED"
    assert c.ok("devices.status")["connection"]["connected"] is False
    assert b.ok("devices.status")["connection"]["device"]["device_id"] == a.device_id


# ------------------------------------------------------------------ offline detection and recovery


def test_offline_peer_is_detected_and_recovers_when_it_returns(pair, tmp_path):
    a, b = pair
    connect_pair(a, b)
    b.ok("receive_mode.set", {"enabled": True})
    b.stop()
    wait_for(
        lambda: a.ok("devices.status")["connection"]["device"]["status"] == "offline",
        what="offline detection",
    )
    snap = a.ok("devices.status")["connection"]
    assert snap["connected"] is False and snap["device"]["device_name"] == "Bob"
    assert "DEVICE_OFFLINE" in audit_set(a)
    fid = a.import_bytes("a.txt", b"hello again", tmp_path)
    out = a.rpc("transfer.create", {"file_ids": [fid], "destination_device_id": b.device_id})
    assert out["error"]["code"] == "DEVICE_OFFLINE"

    b.restart()  # new port, same identity, trust and Receive Mode persisted on disk
    a.see(b)
    wait_for(
        lambda: a.ok("devices.status")["connection"]["connected"] is True, what="peer to come back"
    )
    assert b.ok("receive_mode.get")["enabled"] is True
    done = a.wait_terminal(send(a, b, [fid]))  # Bob accepts without Alice reconnecting
    assert done["status"] == "completed"
    assert list(received(b)) == ["a.txt"]


def test_receiver_going_offline_mid_transfer_fails_it_instead_of_hanging(
    pair, tmp_path, monkeypatch, gate
):
    a, b = pair
    connect_pair(a, b)
    b.ok("receive_mode.set", {"enabled": True})
    pause_upload(monkeypatch, gate)
    fid = a.import_bytes("clip.mp4", os.urandom(200_000), tmp_path)
    tid = send(a, b, [fid])
    assert gate.started.wait(10)

    b.stop()  # the receiver disappears while the upload is in flight
    gate.release.set()

    done = a.wait_terminal(tid, timeout=30)
    assert done["status"] == "failed"
    assert done["error_code"] in {
        "DEVICE_OFFLINE", "CONNECTION_RESET", "PEER_CONNECTION_FAILED", "NETWORK_ERROR",
        "REQUEST_TIMEOUT",
    }  # fmt: skip
    assert done["files"][0]["status"] == "failed"
    assert a.ok("status.snapshot")["active_transfer"] is None
    assert list(a.paths.temp_dir.iterdir()) == []  # archive cleaned up
    assert "TRANSFER_FAILED" in audit_set(a)


def test_sender_crash_mid_upload_leaves_the_receiver_clean_and_failed(
    pair, tmp_path, monkeypatch, gate
):
    a, b = pair
    connect_pair(a, b)
    b.ok("receive_mode.set", {"enabled": True})
    pause_upload(monkeypatch, gate, then_raise=RuntimeError("sender process died"))
    fid = a.import_bytes("clip.mp4", os.urandom(200_000), tmp_path)
    tid = send(a, b, [fid])
    assert gate.started.wait(10)
    gate.release.set()

    assert a.wait_terminal(tid)["status"] == "failed"
    failed = wait_for(
        lambda: (t := b.transfer(tid))["status"] == "failed" and t, what="receiver to give up"
    )
    assert failed["files"][0]["status"] == "failed"
    assert received(b) == {} and list(b.paths.transfers_dir.iterdir()) == []
    assert b.ok("status.snapshot")["active_transfer"] is None
    # and the receiver is immediately usable again
    gate.release.set()
    monkeypatch.undo()
    fid2 = a.import_bytes("ok.txt", b"fine", tmp_path)
    assert a.wait_terminal(send(a, b, [fid2]))["status"] == "completed"


def test_restarted_sender_can_reconnect_and_keep_going(pair, tmp_path):
    a, b = pair
    connect_pair(a, b)
    b.ok("receive_mode.set", {"enabled": True})
    a.restart()
    a.see(b)
    assert a.ok("devices.connect", {"device_id": b.device_id})["connection"]["connected"] is True
    fid = a.import_bytes("after.txt", b"restarted", tmp_path)
    assert a.wait_terminal(send(a, b, [fid]))["status"] == "completed"
    assert a.ok("devices.list")["devices"][0]["is_trusted"] is True


# -------------------------------------------- never trust an unconfirmed success


@pytest.mark.parametrize(
    "reply",
    [
        {"status": "completed"},
        {"status": "completed", "files": []},
        {"status": "completed", "files": [{"name": "other.txt", "status": "completed"}]},
        {"status": "completed", "files": [{"name": "a.txt", "status": "bogus"}]},
        {"status": "completed", "files": [{"name": "a.txt", "status": "failed"}]},
        {"status": "partially_completed", "files": [{"name": "a.txt", "status": "completed"}]},
        "not json at all",
    ],
    ids=[
        "no-files",
        "empty-files",
        "wrong-names",
        "bad-status",
        "lying-completed",
        "lying-partial",
        "garbage",
    ],
)
def test_a_garbled_or_inconsistent_reply_is_never_reported_as_success(
    pair, tmp_path, monkeypatch, reply
):
    import httpx

    a, b = pair
    connect_pair(a, b)
    b.ok("receive_mode.set", {"enabled": True})
    fid = a.import_bytes("a.txt", b"payload", tmp_path)
    response = (
        httpx.Response(200, content=reply.encode())
        if isinstance(reply, str)
        else httpx.Response(200, json=reply)
    )
    monkeypatch.setattr(a.network.sender, "_upload", lambda *args, **kw: response)
    done = a.wait_terminal(send(a, b, [fid]))
    assert done["status"] == "failed" and done["error_code"] == "NETWORK_ERROR"
    assert done["files"][0]["status"] == "failed"


# ------------------------------------------------------------------ the size limit, end to end


@pytest.mark.slow
def test_a_file_of_exactly_50_mb_crosses_the_network_intact(pair, tmp_path):
    import time

    a, b = pair
    connect_pair(a, b)
    b.ok("receive_mode.set", {"enabled": True})
    data = os.urandom(50 * MB)
    fid = a.import_bytes("limit.mp4", data, tmp_path)
    start = time.monotonic()
    done = a.wait_terminal(send(a, b, [fid]), timeout=120)
    elapsed = time.monotonic() - start
    assert done["status"] == "completed"
    wait_for(lambda: len(received(b)) == 1, what="receiver")
    (rec,) = received(b).values()
    assert rec["size"] == 50 * MB
    assert hashlib.sha256(stored_bytes(b, rec["id"])).digest() == hashlib.sha256(data).digest()
    print(f"\n50 MB over loopback HTTPS in {elapsed:.1f}s ({50 / elapsed:.0f} MB/s)")


def test_a_file_one_byte_over_50_mb_is_refused_before_it_is_ever_sent(pair, tmp_path):
    a, b = pair
    connect_pair(a, b)
    b.ok("receive_mode.set", {"enabled": True})
    big = tmp_path / "toobig.mp4"
    with big.open("wb") as f:
        f.truncate(50 * MB + 1)
    out = a.ok("files.add", {"paths": [str(big)]})
    assert out["added"] == [] and out["rejected"][0]["error"]["code"] == "FILE_TOO_LARGE"
    assert a.ok("files.list")["files"] == []

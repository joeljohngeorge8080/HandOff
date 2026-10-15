"""ADR-063: several connected peers, parallel transfers (one per peer), drop = send to all."""

import os

import pytest
from helpers import pause_upload, wait_for

MB = 1024 * 1024
TERMINAL = ("completed", "failed", "partially_completed")


@pytest.fixture
def hub(node_factory):
    """Alice connected to Bob, Carol and Dave (she initiated every connection)."""
    nodes = [node_factory(n) for n in ("Alice", "Bob", "Carol", "Dave")]
    a, *others = nodes
    a.see(*others)
    for o in others:
        o.see(a)
        a.ok("devices.connect", {"device_id": o.device_id})
    return nodes


@pytest.fixture
def src(tmp_path):
    d = tmp_path / "Desktop-files"
    d.mkdir()

    def make(name: str, data: bytes = b"hello") -> str:
        p = d / name
        p.write_bytes(data)
        return str(p)

    return make


def connected_ids(node):
    conn = node.ok("devices.status")["connection"]
    return {d["device_id"] for d in conn["devices"] if d["status"] == "connected"}


def drop_all(node, paths):
    return node.ok("drop.send", {"paths": paths})


def wait_all(node, transfers):
    return [node.wait_terminal(t["transfer_id"]) for t in transfers]


def audit_events(node):
    from handoff.db.repositories import AuditRepository

    with node.core.db.session() as s:
        return [r.event_type for r in AuditRepository(s).list(limit=1000)]


# ------------------------------------------------------------------ connecting


def test_one_device_can_be_connected_to_several_peers(hub):
    a, b, c, d = hub
    assert connected_ids(a) == {b.device_id, c.device_id, d.device_id}
    for peer in (b, c, d):  # each of them sees Alice as a connected peer too
        assert connected_ids(peer) == {a.device_id}
    assert a.ok("devices.status")["connection"]["connected"] is True


def test_a_fifth_device_is_refused_when_four_are_online(node_factory):
    nodes = [node_factory(n) for n in ("Hub", "P1", "P2", "P3", "P4", "P5")]
    hub, *peers = nodes
    hub.see(*peers)
    for p in peers[:4]:
        p.see(hub)
        hub.ok("devices.connect", {"device_id": p.device_id})
    out = hub.rpc("devices.connect", {"device_id": peers[4].device_id})
    assert out["error"]["code"] == "DEVICE_LIMIT_REACHED"
    assert len(connected_ids(hub)) == 4
    assert connected_ids(peers[4]) == set()


def test_a_device_with_full_slots_turns_away_a_new_caller(node_factory):
    nodes = [node_factory(n) for n in ("Hub", "P1", "P2", "P3", "P4", "P5")]
    hub, *peers = nodes
    for p in peers[:4]:
        p.see(hub)
        p.ok("devices.connect", {"device_id": hub.device_id})
    peers[4].see(hub)
    out = peers[4].rpc("devices.connect", {"device_id": hub.device_id})
    assert out["error"]["code"] == "DEVICE_LIMIT_REACHED"
    assert connected_ids(peers[4]) == set()


def test_connecting_while_a_transfer_is_running_is_allowed(node_factory, src, monkeypatch, gate):
    a, b, c = (node_factory(n) for n in ("Alice", "Bob", "Carol"))
    a.see(b, c)
    a.ok("devices.connect", {"device_id": b.device_id})
    pause_upload(monkeypatch, gate)
    drop_all(a, [src("a.png", os.urandom(50_000))])
    assert gate.started.wait(10)
    a.ok("devices.connect", {"device_id": c.device_id})  # used to be INVALID_STATE
    assert connected_ids(a) == {b.device_id, c.device_id}
    gate.release.set()


# ------------------------------------------------------------------ drop = send to all


def test_a_drop_goes_to_every_connected_peer(hub, src):
    a, *peers = hub
    out = drop_all(a, [src("note.txt", b"for everyone")])
    assert len(out["transfers"]) == 3 and out["skipped"] == []
    assert out["transfer"] == out["transfers"][0]  # compatibility with single-peer callers
    assert {t["status"] for t in wait_all(a, out["transfers"])} == {"completed"}
    for p in peers:
        assert p.received_files() == {"note.txt": b"for everyone"}


def test_managed_copies_are_deleted_only_after_every_transfer_ended(hub, src, monkeypatch, gate):
    a, *_ = hub
    pause_upload(monkeypatch, gate)
    out = drop_all(a, [src("big.png", os.urandom(30_000))])
    assert gate.started.wait(10)
    assert len(list(a.paths.files_dir.iterdir())) == 1  # one shared copy, still needed
    gate.release.set()
    wait_all(a, out["transfers"])
    wait_for(lambda: not list(a.paths.files_dir.iterdir()), what="managed copy to be removed")


def test_a_peer_that_is_busy_with_us_is_skipped_not_failed(node_factory, src, monkeypatch, gate):
    a, b, c = (node_factory(n) for n in ("Alice", "Bob", "Carol"))
    a.see(b, c)
    a.ok("devices.connect", {"device_id": b.device_id})
    pause_upload(monkeypatch, gate)
    first = drop_all(a, [src("one.png", os.urandom(40_000))])
    assert gate.started.wait(10)
    a.ok("devices.connect", {"device_id": c.device_id})

    second = drop_all(a, [src("two.png", os.urandom(40_000))])
    assert [t["peer_device_id"] for t in second["transfers"]] == [c.device_id]
    assert [s["device_name"] for s in second["skipped"]] == ["Bob"]
    assert second["skipped"][0]["reason"] == "busy"

    gate.release.set()
    wait_all(a, first["transfers"] + second["transfers"])
    assert set(b.received_files()) == {"one.png"}
    assert set(c.received_files()) == {"two.png"}


def test_a_drop_is_rejected_when_every_peer_is_busy(node_factory, src, monkeypatch, gate):
    a, b = node_factory("Alice"), node_factory("Bob")
    a.see(b)
    a.ok("devices.connect", {"device_id": b.device_id})
    pause_upload(monkeypatch, gate)
    drop_all(a, [src("one.png", os.urandom(40_000))])
    assert gate.started.wait(10)
    before = list(a.paths.files_dir.iterdir())
    out = a.rpc("drop.send", {"paths": [src("two.png")]})
    assert out["error"]["code"] == "INVALID_STATE"
    assert list(a.paths.files_dir.iterdir()) == before  # nothing was copied for the refused drop
    gate.release.set()


def test_validation_is_still_all_or_nothing_with_many_peers(hub, src):
    a, *peers = hub
    out = a.rpc("drop.send", {"paths": [src("fine.txt"), src("setup.exe", b"MZ")]})
    assert "error" in out
    assert list(a.paths.files_dir.iterdir()) == []
    for p in peers:
        assert p.received_files() == {}


def test_only_online_peers_receive_a_drop(hub, src):
    a, b, c, d = hub
    c.stop()
    wait_for(
        lambda: (
            {x["device_id"]: x["status"] for x in a.ok("devices.status")["connection"]["devices"]}[
                c.device_id
            ]
            == "offline"
        ),
        what="Carol to be marked offline",
    )
    out = drop_all(a, [src("n.txt")])
    assert {t["peer_device_id"] for t in out["transfers"]} == {b.device_id, d.device_id}
    wait_all(a, out["transfers"])


# ------------------------------------------------------------------ parallel and isolated


def test_transfers_with_different_peers_run_at_the_same_time_in_both_directions(
    node_factory, src, monkeypatch, gate, tmp_path
):
    a, b, c = (node_factory(n) for n in ("Alice", "Bob", "Carol"))
    a.see(b, c)
    c.see(a)
    a.ok("devices.connect", {"device_id": b.device_id})
    a.ok("devices.connect", {"device_id": c.device_id})
    pause_upload(monkeypatch, gate)

    fid = a.import_bytes("to-b.png", os.urandom(40_000), tmp_path)
    to_b = a.ok("transfer.create", {"file_ids": [fid], "destination_device_id": b.device_id})[
        "transfer"
    ]
    from_c = c.ok("drop.send", {"paths": [src("from-c.png", os.urandom(40_000))]})["transfer"]
    assert gate.started.wait(10)

    def both_running():
        active = a.ok("status.snapshot")["active_transfers"]
        return len(active) == 2 and {t["status"] for t in active} == {"transferring"}

    wait_for(both_running, what="two transfers active at the same time on Alice")
    gate.release.set()
    assert a.wait_terminal(to_b["transfer_id"])["status"] == "completed"
    assert c.wait_terminal(from_c["transfer_id"])["status"] == "completed"
    assert b.received_files().keys() == {"to-b.png"}
    assert a.received_files().keys() == {"from-c.png"}


def test_two_senders_with_the_same_file_name_never_overwrite_each_other(
    node_factory, src, monkeypatch, gate
):
    a, b, c = (node_factory(n) for n in ("Alice", "Bob", "Carol"))
    for s in (b, c):
        s.see(a)
        s.ok("devices.connect", {"device_id": a.device_id})
    pause_upload(monkeypatch, gate)
    d1, d2 = os.urandom(20_000), os.urandom(20_000)
    t1 = b.ok("drop.send", {"paths": [src("photo.png", d1)]})["transfer"]["transfer_id"]
    t2 = c.ok("drop.send", {"paths": [src("photo.png", d2)]})["transfer"]["transfer_id"]
    assert gate.started.wait(10)
    gate.release.set()
    assert b.wait_terminal(t1)["status"] == "completed"
    assert c.wait_terminal(t2)["status"] == "completed"
    got = a.received_files()
    assert set(got) == {"photo.png", "photo(1).png"}
    assert sorted(got.values()) == sorted([d1, d2])


def test_one_peer_going_offline_fails_only_its_own_transfer(node_factory, src, monkeypatch, gate):
    a, b, c = (node_factory(n) for n in ("Alice", "Bob", "Carol"))
    a.see(b, c)
    a.ok("devices.connect", {"device_id": b.device_id})
    a.ok("devices.connect", {"device_id": c.device_id})
    pause_upload(monkeypatch, gate)
    out = drop_all(a, [src("x.png", os.urandom(60_000))])
    assert gate.started.wait(10)
    by_dest = {t["peer_device_id"]: t["transfer_id"] for t in out["transfers"]}

    b.stop()
    wait_for(
        lambda: (
            {x["device_id"]: x["status"] for x in a.ok("devices.status")["connection"]["devices"]}[
                b.device_id
            ]
            == "offline"
        ),
        what="Bob to be marked offline",
    )
    assert a.transfer(by_dest[c.device_id])["status"] == "transferring"  # Carol is unaffected

    gate.release.set()  # the paused uploads now run on: Bob's is aborted, Carol's completes
    assert a.wait_terminal(by_dest[b.device_id])["status"] == "failed"
    assert a.wait_terminal(by_dest[c.device_id])["status"] == "completed"
    assert c.received_files().keys() == {"x.png"}


def test_the_disk_check_counts_what_other_incoming_transfers_already_reserved(
    node_factory, src, monkeypatch, gate
):
    from handoff.transfer import receiver as receiver_mod

    a, b, c = (node_factory(n) for n in ("Alice", "Bob", "Carol"))
    for s in (b, c):
        s.see(a)
        s.ok("devices.connect", {"device_id": a.device_id})
    # Enough room for one 30 KB transfer (needs ~2x + margin) but not for two at once.
    free = 2 * 30_000 + receiver_mod._DISK_SAFETY_MARGIN + 10_000
    monkeypatch.setattr(receiver_mod.ReceiverService, "_free_bytes", lambda self, where=None: free)
    pause_upload(monkeypatch, gate)
    b.ok("drop.send", {"paths": [src("one.png", os.urandom(30_000))]})
    assert gate.started.wait(10)
    out = c.rpc("drop.send", {"paths": [src("two.png", os.urandom(30_000))]})
    transfer = out["result"]["transfer"]
    gate.release.set()
    assert c.wait_terminal(transfer["transfer_id"])["status"] == "failed"
    assert "INSUFFICIENT_STORAGE" in {t["error_code"] for t in c.ok("history.list", {})["items"]}


# ------------------------------------------------------------------ a peer that cannot take the drop


def _fail_create_for(monkeypatch, device_ids, code):
    from handoff.errors import HandOffError
    from handoff.transfer.sender import SenderService

    original = SenderService.create

    def flaky(self, file_ids, destination_device_id):
        if destination_device_id in device_ids:
            raise HandOffError(code, "refused for the test")
        return original(self, file_ids, destination_device_id)

    monkeypatch.setattr(SenderService, "create", flaky)


@pytest.mark.parametrize(
    "code,reason",
    [("DEVICE_OFFLINE", "offline"), ("NETWORK_ERROR", "unreachable"), ("INVALID_STATE", "busy")],
)
def test_a_peer_that_cannot_take_the_drop_is_reported_and_the_others_still_get_it(
    hub, src, monkeypatch, code, reason
):
    a, b, c, d = hub
    _fail_create_for(monkeypatch, {c.device_id}, code)
    out = drop_all(a, [src("n.txt", b"x")])
    assert {t["peer_device_id"] for t in out["transfers"]} == {b.device_id, d.device_id}
    assert [(s["device_name"], s["reason"]) for s in out["skipped"]] == [("Carol", reason)]
    wait_all(a, out["transfers"])
    assert c.received_files() == {} and b.received_files() == {"n.txt": b"x"}


def test_when_no_peer_can_take_the_drop_it_fails_and_leaves_nothing_behind(hub, src, monkeypatch):
    a, b, c, d = hub
    _fail_create_for(monkeypatch, {b.device_id, c.device_id, d.device_id}, "NETWORK_ERROR")
    out = a.rpc("drop.send", {"paths": [src("n.txt")]})
    assert out["error"]["code"] == "NETWORK_ERROR"
    assert list(a.paths.files_dir.iterdir()) == []  # the managed copies were rolled back


def test_a_copy_that_is_already_gone_does_not_break_the_cleanup(hub, src):
    a, *_ = hub
    out = drop_all(a, [src("n.txt")])
    wait_all(a, out["transfers"])
    assert a.network.drops.sweep_orphans() == 0
    a.network.drops._delete(["no-such-file-id"])  # must be silent, not raise

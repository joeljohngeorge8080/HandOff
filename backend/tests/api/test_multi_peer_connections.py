"""ADR-063: up to MAX_CONNECTED_PEERS connected peers, inbound side (peer API)."""

import pytest
from helpers import new_identity

from handoff.config import MAX_CONNECTED_PEERS
from handoff.db.repositories import DeviceRepository


def err(resp):
    return resp.json()["error"]["code"]


@pytest.fixture
def ids(h, tmp_path):
    """Enough signing identities to fill every slot and then one more."""
    return [new_identity(tmp_path, f"peer{i}") for i in range(MAX_CONNECTED_PEERS + 1)]


def connect_all(h, ids, n=MAX_CONNECTED_PEERS):
    for who in ids[:n]:
        assert h.connect(who).status_code == 200


def test_the_limit_is_four_peers():
    assert MAX_CONNECTED_PEERS == 4


def test_several_peers_can_be_connected_at_the_same_time(h, ids):
    connect_all(h, ids)
    snap = h.connections.snapshot()
    assert snap["connected"] is True
    assert {d["device_id"] for d in snap["devices"]} == {i.device_id for i in ids[:4]}
    assert all(d["status"] == "connected" for d in snap["devices"])


def test_the_snapshot_keeps_a_device_field_for_older_clients(h, ids):
    connect_all(h, ids, 2)
    assert h.connections.snapshot()["device"]["device_id"] == ids[0].device_id


def test_an_empty_snapshot_has_no_devices(h):
    assert h.connections.snapshot() == {"connected": False, "devices": [], "device": None}


def test_a_fifth_peer_is_refused_and_not_even_recorded(h, ids):
    connect_all(h, ids)
    r = h.connect(ids[4])
    assert r.status_code == 409 and err(r) == "DEVICE_LIMIT_REACHED"
    with h.core.db.session() as s:
        assert DeviceRepository(s).get_by_device_id(ids[4].device_id) is None
    assert len(h.connections.snapshot()["devices"]) == 4


def test_connecting_the_same_peer_again_does_not_use_another_slot(h, ids):
    connect_all(h, ids)
    assert h.connect(ids[0]).status_code == 200
    assert len(h.connections.snapshot()["devices"]) == 4


def test_an_offline_peers_slot_is_reclaimed_for_a_new_peer(h, ids):
    connect_all(h, ids)
    h.connections._peers[ids[1].device_id].status = "offline"
    assert h.connect(ids[4]).status_code == 200
    seen = {d["device_id"] for d in h.connections.snapshot()["devices"]}
    assert ids[1].device_id not in seen and ids[4].device_id in seen
    with h.core.db.session() as s:  # the evicted device stays trusted: trust is persistent
        row = DeviceRepository(s).get_by_device_id(ids[1].device_id)
        assert row.is_trusted and row.status == "available"


def test_releasing_one_peer_leaves_the_others_connected(h, ids):
    connect_all(h, ids, 3)
    assert h.call("DELETE", "/api/v1/connection", who=ids[1]).json() == {"status": "released"}
    seen = {d["device_id"] for d in h.connections.snapshot()["devices"]}
    assert seen == {ids[0].device_id, ids[2].device_id}
    with h.core.db.session() as s:
        assert DeviceRepository(s).get_by_device_id(ids[1].device_id).is_trusted


def test_a_peer_cannot_release_someone_elses_connection(h, ids):
    connect_all(h, ids, 2)
    h.call("DELETE", "/api/v1/connection", who=ids[1])
    assert ids[0].device_id in {d["device_id"] for d in h.connections.snapshot()["devices"]}


def test_get_connection_lists_every_connected_device(h, ids):
    connect_all(h, ids, 3)
    body = h.call("GET", "/api/v1/connection", who=ids[0]).json()
    assert body["connected"] is True and len(body["devices"]) == 3


def test_a_trusted_peer_may_send_when_a_slot_is_free(h, ids):
    connect_all(h, ids, 2)
    with h.core.db.session() as s:  # trusted earlier, not connected now (e.g. after a restart)
        repo = DeviceRepository(s)
        repo.upsert(ids[2].device_id, "Late", "linux", public_key=ids[2].public_key_b64)
        repo.set_trusted(ids[2].device_id, True)
    h.connections.allow_inbound_transfer(ids[2].device_id)
    assert h.connections.is_connected(ids[2].device_id)
    assert len(h.connections.snapshot()["devices"]) == 3


def test_a_trusted_peer_cannot_send_when_every_slot_holds_an_online_peer(h, ids):
    connect_all(h, ids)
    with h.core.db.session() as s:
        repo = DeviceRepository(s)
        repo.upsert(ids[4].device_id, "Late", "linux", public_key=ids[4].public_key_b64)
        repo.set_trusted(ids[4].device_id, True)
    r = h.start_transfer("tr_x", {"a.txt": b"hello"}, who=ids[4])
    assert r.status_code == 409 and err(r) == "DEVICE_LIMIT_REACHED"
    assert h.transfer_rows() == []


def test_an_untrusted_device_cannot_send_even_with_a_free_slot(h, ids):
    r = h.call("POST", "/api/v1/transfers", who=ids[0], json={"transfer_id": "t", "files": []})
    assert r.status_code == 403 and err(r) == "DEVICE_NOT_TRUSTED"
    assert h.connections.snapshot()["devices"] == []

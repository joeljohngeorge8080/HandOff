from datetime import UTC, datetime, timedelta

import pytest
from helpers import fresh_key

from handoff.db.repositories import DeviceRepository
from handoff.identity import Identity


def err(resp):
    return resp.json()["error"]["code"]


def trust(h, who, name="Bob-Laptop"):
    with h.core.db.session() as s:
        repo = DeviceRepository(s)
        repo.upsert(who.device_id, name, "linux", public_key=who.public_key_b64)
        repo.set_trusted(who.device_id, True)


# ----------------------------------------------------------------------- public surface


def test_health_is_public_and_minimal(h):
    r = h.http.get("/api/v1/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "device_id": h.core.identity.device_id}


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json", "/api/v1/openapi.json"])
def test_no_documentation_or_development_endpoints_exist(h, path):
    r = h.http.get(path)
    assert r.status_code == 404 and err(r) == "INVALID_REQUEST"


def test_unknown_routes_and_methods_use_the_standard_error_format(h):
    h.connect()
    r = h.call("GET", "/api/v1/nope")
    assert r.status_code == 404 and set(r.json()) == {"error"}
    r = h.call("PUT", "/api/v1/device", json={})
    assert r.status_code == 405 and err(r) == "INVALID_REQUEST"


def test_the_receive_mode_endpoint_no_longer_exists(h):
    """ADR-055: Receive Mode was removed, so there is nothing for a peer to read or set."""
    h.connect()
    for method in ("GET", "PUT", "POST"):
        r = h.call(method, "/api/v1/receive-mode", json={"enabled": True})
        assert r.status_code == 404 and err(r) == "INVALID_REQUEST"


def test_request_id_is_echoed_generated_and_sanitized(h):
    assert (
        h.http.get("/api/v1/health", headers={"X-Request-ID": "req_abc-1.2"}).headers[
            "x-request-id"
        ]
        == "req_abc-1.2"
    )
    assert h.http.get("/api/v1/health").headers["x-request-id"].startswith("req_")
    bad = h.http.get("/api/v1/health", headers={"X-Request-ID": "x y\t<script>"})
    assert bad.headers["x-request-id"].startswith("req_")
    assert h.http.get("/nope").headers["x-request-id"]  # also on error responses
    assert h.call("GET", "/api/v1/device").headers["x-request-id"]  # 403 for unknown device


# ----------------------------------------------------------------------- authentication


def test_unsigned_requests_are_rejected_and_audited(h):
    r = h.http.get("/api/v1/device")
    assert r.status_code == 401 and err(r) == "INVALID_SIGNATURE"
    assert any("Missing authentication" in a.message for a in h.audit("INVALID_DEVICE"))


def test_unknown_device_cannot_use_the_api(h):
    r = h.call("GET", "/api/v1/device")  # alice never connected
    assert r.status_code == 403 and err(r) == "DEVICE_NOT_TRUSTED"
    (row,) = h.audit("INVALID_DEVICE")
    assert row.device_id == h.alice.device_id


def test_known_but_untrusted_device_is_rejected(h):
    with h.core.db.session() as s:
        DeviceRepository(s).upsert(
            h.bob.device_id, "Bob", "linux", public_key=h.bob.public_key_b64
        )  # known, is_trusted stays False
    r = h.call("GET", "/api/v1/device", who=h.bob)
    assert r.status_code == 403 and err(r) == "DEVICE_NOT_TRUSTED"


def test_trusted_device_can_read_device_info(h):
    assert h.connect().status_code == 200
    d = h.call("GET", "/api/v1/device").json()
    assert d["device_id"] == h.core.identity.device_id
    assert d["api_version"] == "v1" and d["status"] == "available"
    assert "receive_mode" not in d
    assert "receive_directory" not in d  # a peer must never learn local paths


def test_a_replayed_request_is_rejected(h):
    h.connect()
    headers = h.headers("GET", "/api/v1/device")
    assert h.http.get("/api/v1/device", headers=headers).status_code == 200
    again = h.http.get("/api/v1/device", headers=headers)
    assert again.status_code == 401 and err(again) == "REPLAYED_REQUEST"


@pytest.mark.parametrize("delta", [-120, 120, -3600])
def test_requests_outside_the_time_window_are_rejected(h, delta):
    h.connect()
    r = h.call("GET", "/api/v1/device", now=datetime.now(UTC) + timedelta(seconds=delta))
    assert r.status_code == 401 and err(r) == "INVALID_SIGNATURE"


def test_a_signature_cannot_be_reused_for_another_path_or_method(h):
    h.connect()
    headers = h.headers("GET", "/api/v1/device")
    assert h.http.get("/api/v1/connection", headers=headers).status_code == 401
    headers = h.headers("GET", "/api/v1/connection")
    assert h.http.delete("/api/v1/connection", headers=headers).status_code == 401


def test_signature_made_with_a_different_key_is_rejected(h):
    h.connect()
    forged = Identity(device_id=h.alice.device_id, private_key=h.bob.private_key)
    r = h.call("GET", "/api/v1/device", who=forged)
    assert r.status_code == 401 and err(r) == "INVALID_SIGNATURE"


@pytest.mark.parametrize(
    ("header", "value", "code"),
    [
        ("X-Device-ID", "not-a-uuid", "INVALID_DEVICE_ID"),
        (
            "X-Device-ID",
            "7E7D8C2A-5E9E-4E1C-9A7D-9A1C4E1F7A31",
            "INVALID_DEVICE_ID",
        ),  # not canonical
        ("X-Nonce", "short", "INVALID_SIGNATURE"),
        ("X-Nonce", "bad nonce with spaces!", "INVALID_SIGNATURE"),
        ("X-Timestamp", "yesterday", "INVALID_SIGNATURE"),
        ("X-Signature", "", "INVALID_SIGNATURE"),
        ("X-Signature", "!!!notbase64!!!", "INVALID_SIGNATURE"),
    ],
)
def test_malformed_auth_headers_are_rejected(h, header, value, code):
    h.connect()
    headers = h.headers("GET", "/api/v1/device")
    headers[header] = value
    r = h.http.get("/api/v1/device", headers=headers)
    assert r.status_code in (400, 401) and err(r) == code


def test_repeated_invalid_requests_are_rate_limited_and_audit_stays_bounded(h):
    for _ in range(20):
        assert h.http.get("/api/v1/device").status_code == 401
    r = h.http.get("/api/v1/device")
    assert r.status_code == 429 and err(r) == "RATE_LIMITED"
    h.connect()  # even a legitimate request from the flooding address waits
    assert h.call("GET", "/api/v1/device").status_code == 429
    assert len(h.audit("INVALID_DEVICE")) <= 22  # not one row per attempt forever
    assert any("Too many" in a.message for a in h.audit("INVALID_DEVICE"))


# ----------------------------------------------------------------------- connection / trust


def test_connect_makes_the_caller_trusted_and_returns_our_identity(h):
    r = h.connect()
    assert r.status_code == 200
    body = r.json()
    assert body["device_id"] == h.core.identity.device_id
    assert body["public_key"] == h.core.identity.public_key_b64
    assert body["status"] == "connected" and body["connection_id"].startswith("conn_")
    assert "private" not in r.text.lower()
    with h.core.db.session() as s:
        d = DeviceRepository(s).get_by_device_id(h.alice.device_id)
        assert d.is_trusted and d.public_key == h.alice.public_key_b64
        assert d.last_ip and d.port == 9999 and d.platform == "linux"
    assert [a.device_id for a in h.audit("DEVICE_CONNECTED")] == [h.alice.device_id]
    assert h.call("GET", "/api/v1/connection").json()["device"]["device_id"] == h.alice.device_id


def test_connecting_twice_is_idempotent(h):
    assert h.connect().status_code == 200
    assert h.connect().status_code == 200
    with h.core.db.session() as s:
        assert len(DeviceRepository(s).list_all()) == 1


def test_a_device_whose_key_changed_cannot_reconnect(h):
    h.connect()
    new_key = fresh_key()
    impostor = Identity(device_id=h.alice.device_id, private_key=new_key)
    body = {
        "device_id": impostor.device_id, "device_name": "Evil", "platform": "linux",
        "public_key": impostor.public_key_b64, "port": 1234,
    }  # fmt: skip
    r = h.call("POST", "/api/v1/connection", who=impostor, json=body)
    assert r.status_code == 403 and err(r) == "DEVICE_NOT_TRUSTED"
    with h.core.db.session() as s:
        assert DeviceRepository(s).get_by_device_id(h.alice.device_id).public_key == (
            h.alice.public_key_b64
        )


def test_only_one_peer_can_be_connected_at_a_time(h):
    assert h.connect(h.alice).status_code == 200
    r = h.connect(h.bob)
    assert r.status_code == 409 and err(r) == "DEVICE_ALREADY_CONNECTED"
    with h.core.db.session() as s:
        assert DeviceRepository(s).get_by_device_id(h.bob.device_id) is None  # not even recorded


def test_another_peer_may_connect_once_the_first_is_offline(h):
    h.connect(h.alice)
    h.connections._active.status = "offline"
    assert h.connect(h.bob).status_code == 200
    assert h.connections.snapshot()["device"]["device_id"] == h.bob.device_id
    with h.core.db.session() as s:  # alice stays trusted (trust is persistent)
        assert DeviceRepository(s).get_by_device_id(h.alice.device_id).is_trusted


def test_release_clears_the_active_peer_but_keeps_trust(h):
    h.connect()
    assert h.call("DELETE", "/api/v1/connection").json() == {"status": "released"}
    assert h.call("GET", "/api/v1/connection").json() == {"connected": False, "device": None}
    with h.core.db.session() as s:
        assert DeviceRepository(s).get_by_device_id(h.alice.device_id).is_trusted


def test_other_devices_cannot_release_someone_elses_connection(h):
    h.connect(h.alice)
    trust(h, h.bob)
    h.call("DELETE", "/api/v1/connection", who=h.bob)
    assert h.connections.snapshot()["device"]["device_id"] == h.alice.device_id


@pytest.mark.parametrize(
    "patch",
    [
        {"device_id": "x"},
        {"device_id": 5},
        {"device_name": ""},
        {"device_name": "x" * 65},
        {"device_name": "bad\nname"},
        {"device_name": None},
        {"public_key": "AAAA"},
        {"public_key": "not base64!"},
        {"public_key": 7},
        {"port": 0},
        {"port": 70000},
        {"port": True},
        {"port": "80"},
        {"platform": "x" * 40},
    ],
)
def test_connection_body_is_validated(h, patch):
    body = {
        "device_id": h.alice.device_id, "device_name": "Alice", "platform": "linux",
        "public_key": h.alice.public_key_b64, "port": 8765,
    }  # fmt: skip
    body.update(patch)
    r = h.call("POST", "/api/v1/connection", json=body)
    assert r.status_code == 400 and err(r) in {"INVALID_REQUEST", "INVALID_DEVICE_ID"}
    with h.core.db.session() as s:
        assert DeviceRepository(s).list_all() == []


def test_body_device_id_must_match_the_signing_device(h):
    body = {
        "device_id": h.bob.device_id, "device_name": "Bob", "platform": "linux",
        "public_key": h.alice.public_key_b64, "port": 8765,
    }  # fmt: skip
    r = h.call("POST", "/api/v1/connection", json=body)  # signed by alice, claims to be bob
    assert r.status_code == 400 and err(r) == "INVALID_DEVICE_ID"


def test_connection_body_with_a_mismatched_key_fails_the_signature(h):
    body = {
        "device_id": h.alice.device_id, "device_name": "Alice", "platform": "linux",
        "public_key": h.bob.public_key_b64, "port": 8765,
    }  # fmt: skip
    r = h.call("POST", "/api/v1/connection", json=body)  # alice signs, but claims bob's key
    assert r.status_code == 401 and err(r) == "INVALID_SIGNATURE"


@pytest.mark.parametrize("content", [b"{not json", b"[1,2]", b'"x"', b""])
def test_unparseable_bodies_are_rejected(h, content):
    r = h.call("POST", "/api/v1/connection", content=content)
    assert r.status_code == 400 and err(r) == "INVALID_REQUEST"


def test_oversized_json_bodies_are_rejected_with_413(h):
    r = h.call("POST", "/api/v1/connection", content=b"{" + b" " * (2 * 1024 * 1024) + b"}")
    assert r.status_code == 413

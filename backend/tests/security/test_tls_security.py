import httpx
import pytest

from handoff.devices.client import PeerClient, PinMismatchError
from handoff.errors import HandOffError
from handoff.tls import fetch_server_cert_der, public_key_b64_from_cert_der


@pytest.fixture
def pair(node_factory):
    a, b = node_factory("Alice"), node_factory("Bob")
    return a, b


def url(node, path="/api/v1/health", scheme="https"):
    return f"{scheme}://127.0.0.1:{node.network.port}{path}"


def test_the_server_certificate_belongs_to_the_device_identity(pair):
    _, b = pair
    der = fetch_server_cert_der("127.0.0.1", b.network.port)
    assert public_key_b64_from_cert_der(der) == b.core.identity.public_key_b64


def test_a_pinned_client_can_talk_to_the_right_device(pair):
    a, b = pair
    client = PeerClient(a.core.identity)
    r = client.request(
        "127.0.0.1", b.network.port, "GET", "/api/v1/health",
        expected_key=b.core.identity.public_key_b64, signed=False,
    )  # fmt: skip
    assert r.status_code == 200 and r.json()["device_id"] == b.device_id


def test_the_server_is_not_trusted_by_default_system_roots(pair):
    _, b = pair
    with pytest.raises(httpx.ConnectError):
        httpx.get(url(b), verify=True, timeout=5)


def test_plaintext_http_is_unavailable(pair):
    _, b = pair
    with pytest.raises(httpx.HTTPError):
        httpx.get(url(b, scheme="http"), timeout=5)


def test_a_client_expecting_another_key_refuses_to_talk(pair):
    a, b = pair
    client = PeerClient(a.core.identity)
    with pytest.raises(PinMismatchError) as e:
        client.request(
            "127.0.0.1", b.network.port, "GET", "/api/v1/health",
            expected_key=a.core.identity.public_key_b64, signed=False,
        )  # fmt: skip
    assert e.value.code == "PEER_CONNECTION_FAILED"


def test_nothing_is_sent_to_an_endpoint_that_failed_verification(pair):
    a, b = pair
    sent = []

    def spy():
        sent.append(1)
        yield b"payload"

    client = PeerClient(a.core.identity)
    with pytest.raises(PinMismatchError):
        client.request(
            "127.0.0.1", b.network.port, "POST", "/api/v1/transfers/x/data",
            expected_key=a.core.identity.public_key_b64, content=spy(),
        )  # fmt: skip
    assert sent == []  # the body generator was never even started


def test_unsigned_requests_over_tls_are_still_rejected(pair):
    a, b = pair
    client = PeerClient(a.core.identity)
    r = client.request(
        "127.0.0.1", b.network.port, "GET", "/api/v1/device",
        expected_key=b.core.identity.public_key_b64, signed=False,
    )  # fmt: skip
    assert r.status_code == 401


def test_a_signed_request_from_an_unknown_device_is_rejected_over_tls(pair):
    a, b = pair
    client = PeerClient(a.core.identity)
    r = client.request(
        "127.0.0.1", b.network.port, "GET", "/api/v1/device",
        expected_key=b.core.identity.public_key_b64,
    )  # fmt: skip
    assert r.status_code == 403 and r.json()["error"]["code"] == "DEVICE_NOT_TRUSTED"


def test_unreachable_devices_report_a_clear_error(pair):
    a, _ = pair
    client = PeerClient(a.core.identity)
    with pytest.raises(HandOffError) as e:
        client.request(
            "127.0.0.1", 1, "GET", "/api/v1/health",
            expected_key=a.core.identity.public_key_b64, signed=False,
        )  # fmt: skip
    assert e.value.code == "PEER_CONNECTION_FAILED"


def test_a_restarted_peer_with_a_new_certificate_is_still_trusted_by_key(pair):
    """The certificate is regenerated on every start; the pinned thing is the identity key."""
    a, b = pair
    client = PeerClient(a.core.identity)
    key = b.core.identity.public_key_b64
    before = fetch_server_cert_der("127.0.0.1", b.network.port)
    b.restart()  # new certificate (new serial/validity), same key, new port
    after = fetch_server_cert_der("127.0.0.1", b.network.port)
    assert before != after
    r = client.request(
        "127.0.0.1", b.network.port, "GET", "/api/v1/health", expected_key=key, signed=False
    )
    assert r.status_code == 200

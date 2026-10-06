import socket
import uuid

import pytest
from zeroconf import ServiceInfo

from handoff.config import SERVICE_TYPE
from handoff.devices.discovery import (
    DiscoveredPeer,
    StaticDiscovery,
    _parse_service,
)

OWN = str(uuid.uuid4())
OTHER = str(uuid.uuid4())


def record(**over):
    props = {
        "device_id": OTHER, "device_name": "Aaron-Laptop", "api_port": "8765",
        "api_version": "v1", "fp": "KEY",
    }  # fmt: skip
    port = over.pop("port", 8765)
    addresses = over.pop("addresses", [socket.inet_aton("192.168.1.15")])
    props.update(over)
    props = {k: v for k, v in props.items() if v is not None}
    return ServiceInfo(
        SERVICE_TYPE, f"{OTHER}.{SERVICE_TYPE}", addresses=addresses, port=port, properties=props
    )


def test_a_valid_record_becomes_a_peer():
    p = _parse_service(record(), OWN)
    assert p == DiscoveredPeer(OTHER, "Aaron-Laptop", "192.168.1.15", 8765, "v1", "KEY")


def test_our_own_record_is_ignored():
    assert _parse_service(record(device_id=OWN), OWN) is None


@pytest.mark.parametrize(
    "over",
    [
        {"device_id": "not-a-uuid"},
        {"device_id": OTHER.upper()},  # not canonical
        {"device_id": None},
        {"fp": None},
        {"device_name": None},
        {"device_name": ""},
        {"port": 0},
        {"addresses": []},
    ],
)
def test_malformed_records_are_ignored(over):
    assert _parse_service(record(**over), OWN) is None


def test_overlong_names_are_truncated():
    assert len(_parse_service(record(device_name="x" * 200), OWN).device_name) == 64


def test_static_discovery_lists_gets_and_replaces():
    a = DiscoveredPeer(OTHER, "B-name", "10.0.0.2", 1, "v1", "k")
    c = DiscoveredPeer(str(uuid.uuid4()), "A-name", "10.0.0.3", 2, "v1", "k")
    d = StaticDiscovery([a])
    assert d.peers() == [a] and d.get(OTHER) == a and d.get("nope") is None
    d.set_peers([a, c])
    assert [p.device_name for p in d.peers()] == ["A-name", "B-name"]  # sorted by name
    d.set_peers([])
    assert d.peers() == []

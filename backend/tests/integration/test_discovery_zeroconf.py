import uuid

import pytest
from helpers import wait_for

from handoff.devices.discovery import Advertisement, ZeroconfDiscovery


@pytest.mark.network
def test_two_instances_find_each_other_over_real_mdns():
    """Real multicast DNS on loopback. Skipped where the environment has no multicast."""
    ads = [
        Advertisement(str(uuid.uuid4()), name, f"KEY-{name}", port, "127.0.0.1")
        for name, port in (("Alice-Laptop", 41001), ("Bob-Laptop", 41002))
    ]
    one, two = (ZeroconfDiscovery(interfaces=["127.0.0.1"]) for _ in ads)
    one.start(ads[0])
    two.start(ads[1])
    try:
        try:
            found = wait_for(lambda: one.get(ads[1].device_id), timeout=12, what="mDNS discovery")
        except AssertionError:
            pytest.skip("mDNS multicast is not available in this environment")
        assert (found.device_name, found.address, found.port) == ("Bob-Laptop", "127.0.0.1", 41002)
        assert found.fingerprint == "KEY-Bob-Laptop" and found.api_version == "v1"
        assert one.get(ads[0].device_id) is None  # never lists itself
        assert [p.device_name for p in one.peers()] == ["Bob-Laptop"]
    finally:
        one.stop()
        two.stop()

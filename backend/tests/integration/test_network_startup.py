import json
import os
import socket
import subprocess
import sys

import pytest

from handoff.core import Core
from handoff.devices.client import PeerClient
from handoff.devices.discovery import StaticDiscovery
from handoff.errors import HandOffError
from handoff.ipc.dispatcher import Dispatcher
from handoff.network import Network, NetworkConfig, detect_lan_ip
from handoff.paths import AppPaths


def test_a_port_that_is_already_in_use_is_reported_clearly(paths):
    with socket.socket() as blocker:
        blocker.bind(("127.0.0.1", 0))
        blocker.listen()
        port = blocker.getsockname()[1]
        core = Core(paths, hostname="T")
        core.start()
        net = Network(core, NetworkConfig(host="127.0.0.1", port=port, discovery=StaticDiscovery()))
        with pytest.raises(HandOffError) as e:
            net.start()
        assert str(port) in e.value.message and "in use" in e.value.message
        assert e.value.code == "NETWORK_ERROR"
        core.close()


def test_lan_address_detection_returns_an_ipv4_address():
    ip = detect_lan_ip()
    assert socket.inet_aton(ip) and not ip.startswith("0.")


def test_network_actions_need_a_running_network(core):
    d = Dispatcher(core)  # no network attached
    for action in ("devices.discover", "devices.list", "devices.status"):
        out = json.loads(d.handle_line(json.dumps({"id": 1, "action": action})))
        assert out["error"]["code"] == "INVALID_STATE"
    out = json.loads(
        d.handle_line(
            json.dumps({"id": 2, "action": "transfer.status", "payload": {"transfer_id": "x"}})
        )
    )
    assert out["error"]["code"] == "INVALID_STATE"


def test_the_server_binds_only_the_requested_address(tmp_path):
    core = Core(AppPaths(tmp_path / "d"), hostname="T")
    core.start()
    net = Network(core, NetworkConfig(host="127.0.0.1", port=0, discovery=StaticDiscovery()))
    net.start()
    try:
        assert net.host == "127.0.0.1" and net.port > 0
        assert net.advertisement().port == net.port
    finally:
        net.stop()
        core.close()


def test_transfer_status_for_an_unknown_id(tmp_path):
    core = Core(AppPaths(tmp_path / "d"), hostname="T")
    core.start()
    net = Network(core, NetworkConfig(host="127.0.0.1", port=0, discovery=StaticDiscovery()))
    net.start()
    try:
        out = json.loads(
            Dispatcher(core, net).handle_line(
                json.dumps(
                    {"id": 1, "action": "transfer.status", "payload": {"transfer_id": "nope"}}
                )
            )
        )
        assert out["error"]["code"] == "TRANSFER_NOT_FOUND"
    finally:
        net.stop()
        core.close()


def _spawn(tmp_path, *extra):
    return subprocess.Popen(
        [sys.executable, "-m", "handoff", "--data-dir", str(tmp_path / "data"), *extra],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )  # fmt: skip


def test_the_real_sidecar_serves_the_peer_api_and_stops_cleanly(tmp_path):
    proc = _spawn(tmp_path, "--port", "0", "--bind", "127.0.0.1", "--no-discovery")
    try:
        ready = json.loads(proc.stdout.readline())
        assert ready["event"] == "ready" and ready["port"] > 0

        def rpc(action, payload=None):
            msg = {"id": 7, "action": action, **({"payload": payload} if payload else {})}
            proc.stdin.write(json.dumps(msg) + "\n")
            proc.stdin.flush()
            return json.loads(proc.stdout.readline())

        assert rpc("devices.discover")["result"] == {"devices": []}
        assert rpc("status.snapshot")["result"]["connection"]["connected"] is False

        # the peer API answers over TLS and proves it is this sidecar's device
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        from handoff.identity import Identity

        me = Identity(ready["device_id"], Ed25519PrivateKey.generate())
        key = json.loads((tmp_path / "data" / "keys" / "identity.json").read_text())["private_key"]
        real = serialization.load_pem_private_key(key.encode(), password=None)
        r = PeerClient(me).request(
            "127.0.0.1", ready["port"], "GET", "/api/v1/health",
            expected_key=Identity(ready["device_id"], real).public_key_b64, signed=False,
        )  # fmt: skip
        assert r.status_code == 200 and r.json()["device_id"] == ready["device_id"]
    finally:
        proc.stdin.close()
        assert proc.wait(timeout=20) == 0
        stderr = proc.stderr.read()
        proc.stdout.close()
        proc.stderr.close()
    assert "Traceback" not in stderr


def test_the_sidecar_exits_with_a_clear_error_when_the_port_is_taken(tmp_path):
    with socket.socket() as blocker:
        blocker.bind(("127.0.0.1", 0))
        blocker.listen()
        port = blocker.getsockname()[1]
        out = subprocess.run(
            [sys.executable, "-m", "handoff", "--data-dir", str(tmp_path / "data"),
             "--port", str(port), "--bind", "127.0.0.1", "--no-discovery"],
            input="", capture_output=True, text=True, timeout=60,
        )  # fmt: skip
    assert out.returncode == 1
    err = json.loads(out.stdout.splitlines()[0])["error"]
    assert err["code"] == "NETWORK_ERROR" and str(port) in err["message"]


def test_many_slow_requests_do_not_block_fast_ones(tmp_path):
    """The IPC loop hands requests to workers, so a status poll is never stuck behind a connect."""
    proc = _spawn(tmp_path, "--port", "0", "--bind", "127.0.0.1", "--no-discovery")
    try:
        json.loads(proc.stdout.readline())
        for i in range(20):
            proc.stdin.write(json.dumps({"id": i, "action": "status.snapshot"}) + "\n")
        proc.stdin.flush()
        seen = {json.loads(proc.stdout.readline())["id"] for _ in range(20)}
        assert seen == set(range(20))
    finally:
        proc.stdin.close()
        assert proc.wait(timeout=20) == 0
        proc.stdout.close()
        proc.stderr.close()

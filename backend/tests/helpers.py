from __future__ import annotations

import hashlib
import io
import json
import threading
import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

import handoff.transfer.sender as sender_mod
from handoff.core import Core
from handoff.db.models import Transfer, TransferFile, utcnow
from handoff.db.repositories import DeviceRepository, TransferRepository
from handoff.devices.client import PeerClient
from handoff.devices.connection import ConnectionManager
from handoff.devices.discovery import StaticDiscovery
from handoff.identity import Identity, load_or_create_identity
from handoff.ipc.dispatcher import Dispatcher
from handoff.network import Network, NetworkConfig
from handoff.paths import AppPaths
from handoff.peer_api.app import PeerContext, create_app
from handoff.peer_api.auth import Authenticator
from handoff.peer_api.signing import FailureLimiter, NonceCache, sign_request
from handoff.transfer.receiver import ReceiverService

SHA = "0" * 64


def add_peer(core, device_id: str = "peer-1", name: str = "Aaron-Laptop") -> str:
    with core.db.session() as s:
        DeviceRepository(s).upsert(device_id, name, "linux", last_ip="192.168.1.15", port=8765)
    return device_id


def add_transfer(
    core,
    transfer_id: str = "tr_1",
    *,
    status: str = "completed",
    direction: str = "sent",
    peer: str | None = "peer-1",
    created_at: datetime | None = None,
    files: list[tuple[str, str, str | None]] | None = None,  # (name, file_status, file_id)
) -> str:
    """Seed one transfer row (and its files) directly through the repository."""
    now = created_at or utcnow()
    files = files or [("photo.jpg", "completed", None)]
    with core.db.session() as s:
        t = Transfer(
            id=transfer_id,
            direction=direction,
            source_device_id=None if direction == "sent" else peer,
            destination_device_id=peer if direction == "sent" else None,
            status=status,
            file_count=len(files),
            total_size_bytes=10 * len(files),
            created_at=now,
            completed_at=now if status in ("completed", "failed", "partially_completed") else None,
        )
        rows = [
            TransferFile(
                file_id=fid,
                original_name=name,
                size_bytes=10,
                sha256=SHA,
                status=fstatus,
                created_at=now,
            )
            for name, fstatus, fid in files
        ]
        TransferRepository(s).add(t, rows)
    return transfer_id


# --------------------------------------------------------------------------- networking helpers
def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


def transfer_body(tid: str, files: dict[str, bytes], *, source: str, **over) -> dict:
    body = {
        "transfer_id": tid,
        "source_device_id": source,
        "file_count": len(files),
        "total_size": sum(len(d) for d in files.values()),
        "archive_name": f"handoff-{tid}.zip",
        "files": [
            {"file_id": f"f{i}", "filename": n, "size": len(d), "sha256": sha(d)}
            for i, (n, d) in enumerate(files.items())
        ],
    }
    body.update(over)
    return body


def new_identity(tmp_path: Path, name: str) -> Identity:
    p = AppPaths(tmp_path / f"id-{name}")
    p.ensure()
    return load_or_create_identity(p)


class PeerHarness:
    """The receiver's HTTP app without sockets, plus identities that can sign requests."""

    def __init__(self, tmp_path: Path) -> None:
        self.core = Core(AppPaths(tmp_path / "receiver"), hostname="Receiver")
        self.core.start()
        self.inbox = tmp_path / "receiver-inbox"
        self.inbox.mkdir()
        self.core.settings.set_receive_directory(str(self.inbox))
        self.mono = 1000.0
        self.limiter = FailureLimiter()
        self.client = PeerClient(self.core.identity)
        self.connections = ConnectionManager(
            self.core, self.client, StaticDiscovery(), lambda: 8765, health_interval=3600
        )
        self.receiver = ReceiverService(
            self.core, self.connections, self.limiter, clock=lambda: self.mono, accept_timeout=30
        )
        self.auth = Authenticator(self.core, NonceCache(), self.limiter)
        self.ctx = PeerContext(self.core, self.connections, self.receiver, self.auth)
        self.app = create_app(self.ctx)
        self.http = TestClient(self.app, raise_server_exceptions=False)
        self.alice = new_identity(tmp_path, "alice")
        self.bob = new_identity(tmp_path, "bob")

    def close(self) -> None:
        self.core.close()

    def headers(self, method, path, who: Identity | None = None, *, now=None, nonce=None) -> dict:
        return sign_request(who or self.alice, method, path, now=now, nonce=nonce)

    def call(
        self, method, path, *, who=None, sign=True, json=None, content=None, headers=None, **kw
    ):
        h = dict(headers or {})
        if sign:
            h.update(
                self.headers(
                    method, path, who, now=kw.pop("now", None), nonce=kw.pop("nonce", None)
                )
            )
        return self.http.request(method, path, json=json, content=content, headers=h)

    def connect(self, who: Identity | None = None, port: int = 9999):
        who = who or self.alice
        body = {
            "device_id": who.device_id,
            "device_name": "Alice-Laptop" if who is self.alice else "Bob-Laptop",
            "public_key": who.public_key_b64,
            "platform": "linux",
            "port": port,
        }
        return self.call("POST", "/api/v1/connection", who=who, json=body)

    def start_transfer(self, tid: str, files: dict[str, bytes], who=None, key=None, **over):
        who = who or self.alice
        h = {"Idempotency-Key": key} if key else {}
        return self.call(
            "POST", "/api/v1/transfers", who=who, headers=h,
            json=transfer_body(tid, files, source=who.device_id, **over),
        )  # fmt: skip

    def upload(self, tid: str, payload: bytes, who=None, ctype="application/zip"):
        return self.call(
            "POST", f"/api/v1/transfers/{tid}/data", who=who, content=payload,
            headers={"Content-Type": ctype},
        )  # fmt: skip

    def audit(self, event: str | None = None):
        from handoff.db.repositories import AuditRepository

        with self.core.db.session() as s:
            return AuditRepository(s).list(event_type=event, limit=500)

    def transfer_rows(self):
        from sqlalchemy import select

        from handoff.db.models import Transfer

        with self.core.db.session() as s:
            return list(s.scalars(select(Transfer)))


def wait_for(predicate, timeout: float = 15.0, interval: float = 0.05, what: str = "condition"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    raise AssertionError(f"timed out waiting for {what}")


class TestNode:
    """A full HandOff node: Core + real HTTPS peer API on 127.0.0.1 + static discovery."""

    __test__ = False

    def __init__(self, tmp_path: Path, name: str, **net) -> None:
        self.name = name
        self.paths = AppPaths(tmp_path / name)
        self.net_kwargs = {"health_interval": 0.1, "failure_threshold": 2, **net}
        self.discovery = StaticDiscovery()
        self._boot()

    def _boot(self) -> None:
        self.stopped = False
        self.core = Core(self.paths, hostname=self.name)
        self.core.start()
        self.inbox = self.paths.root.parent / f"{self.name}-inbox"
        self.inbox.mkdir(exist_ok=True)
        self.core.settings.set_receive_directory(str(self.inbox))
        self.network = Network(
            self.core,
            NetworkConfig(host="127.0.0.1", port=0, discovery=self.discovery, **self.net_kwargs),
        )
        self.network.start()
        self.dispatcher = Dispatcher(self.core, self.network)

    def stop(self) -> None:
        if self.stopped:
            return
        self.stopped = True
        self.network.stop()
        self.core.close()

    def restart(self) -> None:
        self.stop()
        self._boot()

    @property
    def device_id(self) -> str:
        return self.core.identity.device_id

    def see(self, *others: TestNode) -> None:
        self.discovery.set_peers([o.network.advertisement() for o in others])

    def rpc(self, action: str, payload: dict | None = None) -> dict:
        msg = {"id": 1, "action": action}
        if payload is not None:
            msg["payload"] = payload
        return json.loads(self.dispatcher.handle_line(json.dumps(msg)))

    def ok(self, action: str, payload: dict | None = None) -> dict:
        out = self.rpc(action, payload)
        assert "result" in out, out
        return out["result"]

    def import_bytes(self, name: str, data: bytes, tmp_path: Path) -> str:
        src = tmp_path / f"src-{self.name}"
        src.mkdir(exist_ok=True)
        (src / name).write_bytes(data)
        return self.ok("files.add", {"paths": [str(src / name)]})["added"][0]["id"]

    def received_files(self) -> dict[str, bytes]:
        """Files written to this node's receive folder, by name."""
        return {p.name: p.read_bytes() for p in self.inbox.iterdir() if p.is_file()}

    def transfer(self, tid: str) -> dict:
        return self.ok("transfer.status", {"transfer_id": tid})["transfer"]

    def wait_terminal(self, tid: str, timeout: float = 20.0) -> dict:
        return wait_for(
            lambda: (
                (t := self.transfer(tid))["status"]
                in ("completed", "failed", "partially_completed")
                and t
            ),
            timeout,
            what=f"transfer {tid} to finish",
        )


def connect_pair(a: TestNode, b: TestNode) -> None:
    a.see(b)
    b.see(a)
    a.ok("devices.connect", {"device_id": b.device_id})


def utc_iso(dt: datetime | None = None) -> str:
    return (dt or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ")


def fresh_key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


class Gate:
    """Lets a test pause an upload mid-stream and then release it."""

    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()


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

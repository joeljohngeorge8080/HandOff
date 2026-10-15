"""Connection management for up to MAX_CONNECTED_PEERS peers (ADR-063, FR-011..FR-016).

Connecting a device adds it; nothing is disconnected and a running transfer never blocks it.
When every slot is taken the request is refused (`DEVICE_LIMIT_REACHED`), except that an
*offline* peer's slot is reclaimed. A peer that stops answering health checks is marked offline
(and only its own running uploads are aborted); when it answers again it is restored without a
new handshake because trust is persistent.
"""

from __future__ import annotations

import logging
import platform as _platform
import threading
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from handoff.audit import AuditEvent, record_event
from handoff.config import (
    HEALTH_FAILURE_THRESHOLD,
    HEALTH_INTERVAL_SECONDS,
    MAX_CONNECTED_PEERS,
)
from handoff.core import Core
from handoff.db.repositories import DeviceRepository
from handoff.devices.client import PeerClient, PinMismatchError, error_from_response
from handoff.devices.discovery import Discovery
from handoff.errors import HandOffError
from handoff.history import PeriodicTask
from handoff.tls import keys_equal

log = logging.getLogger(__name__)

PLATFORM = _platform.system().lower() or "unknown"


@dataclass
class ActivePeer:
    device_id: str
    device_name: str
    address: str | None
    port: int | None
    public_key: str
    status: str = "connected"  # connected | offline
    fail_count: int = field(default=0, repr=False)

    def endpoint(self) -> tuple[str, int]:
        if not self.address or not self.port:
            raise HandOffError("DEVICE_OFFLINE", f"{self.device_name} has no known address.")
        return self.address, self.port

    def as_dict(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "device_name": self.device_name,
            "address": self.address,
            "port": self.port,
            "status": self.status,
        }


class ConnectionManager:
    def __init__(
        self,
        core: Core,
        client: PeerClient,
        discovery: Discovery,
        own_port: Callable[[], int],
        *,
        health_interval: float = HEALTH_INTERVAL_SECONDS,
        failure_threshold: int = HEALTH_FAILURE_THRESHOLD,
    ) -> None:
        self.core, self.client, self.discovery, self._own_port = core, client, discovery, own_port
        self._interval, self._threshold = health_interval, failure_threshold
        self._lock = threading.RLock()
        self._peers: dict[str, ActivePeer] = {}
        self._monitor: PeriodicTask | None = None
        self._prober = ThreadPoolExecutor(
            max_workers=MAX_CONNECTED_PEERS, thread_name_prefix="peer-probe"
        )
        self.on_offline: Callable[[str], None] | None = None
        self._published: dict[str, Any] | None = None

    def publish_if_changed(self) -> None:
        """Push `connection.changed` to the UI when the visible connection state changed."""
        snap = self.snapshot()
        with self._lock:
            if snap == self._published:
                return
            self._published = snap
        self.core.events.publish("connection.changed", snap)

    # ----- lifecycle ---------------------------------------------------------------------

    def start(self) -> None:
        self._monitor = PeriodicTask(self._tick, self._interval, "peer-health")
        self._monitor.start()

    def stop(self) -> None:
        if self._monitor:
            self._monitor.stop()
        self._prober.shutdown(wait=False, cancel_futures=True)

    # ----- state --------------------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            devices = [p.as_dict() for p in self._peers.values()]
            online = [d for d in devices if d["status"] == "connected"]
            shown = online[0] if online else (devices[0] if devices else None)
            return {"connected": bool(online), "devices": devices, "device": shown}

    def peers(self) -> list[ActivePeer]:
        """Every peer holding a slot, online or not."""
        with self._lock:
            return list(self._peers.values())

    def connected_peers(self) -> list[ActivePeer]:
        """Peers that can be sent to right now."""
        with self._lock:
            return [p for p in self._peers.values() if p.status == "connected"]

    def get(self, device_id: str) -> ActivePeer | None:
        with self._lock:
            return self._peers.get(device_id)

    def is_connected(self, device_id: str) -> bool:
        with self._lock:
            peer = self._peers.get(device_id)
            return peer is not None and peer.status == "connected"

    def require_connected(self, device_id: str) -> ActivePeer:
        with self._lock:
            a = self._peers.get(device_id)
            if a is None:
                raise HandOffError("DEVICE_NOT_FOUND", "That device is not connected.")
            if a.status != "connected" or not a.address or not a.port:
                raise HandOffError("DEVICE_OFFLINE", f"{a.device_name} is offline.")
            return a

    def _admit(self, device_id: str) -> None:
        """Make room for `device_id` (caller holds the lock). Offline peers give up their slot."""
        if device_id in self._peers or len(self._peers) < MAX_CONNECTED_PEERS:
            return
        for gone in self._peers.values():
            if gone.status != "connected":
                del self._peers[gone.device_id]
                with self.core.db.write_session() as s:
                    row = DeviceRepository(s).get_by_device_id(gone.device_id)
                    if row:
                        row.status = "available"
                return
        raise HandOffError(
            "DEVICE_LIMIT_REACHED",
            f"Up to {MAX_CONNECTED_PEERS} devices can be connected at the same time.",
        )

    # ----- listing ------------------------------------------------------------------------

    def discover(self) -> list[dict[str, Any]]:
        with self.core.db.session() as s:
            known = {d.device_id: d for d in DeviceRepository(s).list_all()}
        out = []
        for p in self.discovery.peers():
            status = "available"
            if (held := self.get(p.device_id)) is not None:
                status = held.status
            out.append(
                {
                    "device_id": p.device_id,
                    "device_name": p.device_name,
                    "address": p.address,
                    "port": p.port,
                    "status": status,
                    "is_trusted": bool(p.device_id in known and known[p.device_id].is_trusted),
                }
            )
        return out

    def list_known(self) -> list[dict[str, Any]]:
        with self.core.db.session() as s:
            rows = DeviceRepository(s).list_all()
            devices = [
                {
                    "device_id": d.device_id,
                    "device_name": d.device_name,
                    "platform": d.platform,
                    "address": d.last_ip,
                    "port": d.port,
                    "is_trusted": d.is_trusted,
                    "status": d.status,
                }
                for d in rows
            ]
        for d in devices:
            if (held := self.get(str(d["device_id"]))) is not None:
                d["status"] = held.status
        return devices

    # ----- outbound connect (user clicked Connect) ----------------------------------------

    def connect(self, device_id: str) -> dict[str, Any]:
        try:
            return self._connect(device_id)
        finally:
            self.publish_if_changed()

    def _connect(self, device_id: str) -> dict[str, Any]:
        with self._lock:
            peer = self.discovery.get(device_id)
            if peer is None:
                raise HandOffError("DEVICE_NOT_FOUND", "That device is no longer visible.")
            held = self._peers.get(device_id)
            if held and held.status == "connected":
                return self.snapshot()
            self._admit(device_id)

            with self.core.db.session() as s:
                row = DeviceRepository(s).get_by_device_id(device_id)
                stored_key = row.public_key if row and row.is_trusted else None
            expected = stored_key or peer.fingerprint
            if stored_key and not keys_equal(stored_key, peer.fingerprint):
                self._audit_tls_failure(device_id, "Device identity changed since it was trusted.")
                raise PinMismatchError

            try:
                self.client.pin(peer.address, peer.port, expected)
                resp = self.client.request(
                    peer.address,
                    peer.port,
                    "POST",
                    "/api/v1/connection",
                    expected_key=expected,
                    json={
                        "device_id": self.core.identity.device_id,
                        "device_name": self.core.device_name,
                        "public_key": self.core.identity.public_key_b64,
                        "platform": PLATFORM,
                        "port": self._own_port(),
                    },
                )
            except PinMismatchError:
                self._audit_tls_failure(device_id, "Device certificate did not match its identity.")
                raise
            if resp.status_code != 200:
                raise error_from_response(resp)
            body = resp.json()
            if body.get("device_id") != device_id or not keys_equal(
                str(body.get("public_key", "")), expected
            ):
                self._audit_tls_failure(device_id, "Peer answered with a different identity.")
                raise PinMismatchError
            name = str(body.get("device_name") or peer.device_name)[:64]

            with self.core.db.write_session() as s:
                repo = DeviceRepository(s)
                repo.upsert(
                    device_id,
                    name,
                    str(body.get("platform") or "unknown")[:32],
                    last_ip=peer.address,
                    port=peer.port,
                    public_key=expected,
                    status="connected",
                )
                repo.set_trusted(device_id, True)
                record_event(
                    s, AuditEvent.DEVICE_CONNECTED, f"Connected to {name}.", device_id=device_id
                )
            self._peers[device_id] = ActivePeer(device_id, name, peer.address, peer.port, expected)
            return self.snapshot()

    def _audit_tls_failure(self, device_id: str, message: str) -> None:
        with self.core.db.session() as s:
            record_event(s, AuditEvent.TLS_FAILURE, message, device_id=device_id)

    # ----- inbound (a peer called us) -----------------------------------------------------

    def accept_inbound(
        self, device_id: str, name: str, public_key: str, address: str, port: int, platform: str
    ) -> dict[str, Any]:
        """POST /connection: the connecting user's click makes the device trusted (FR-011)."""
        with self._lock:
            self._admit(device_id)
            with self.core.db.write_session() as s:
                repo = DeviceRepository(s)
                repo.upsert(
                    device_id, name, platform, last_ip=address, port=port,
                    public_key=public_key, status="connected",
                )  # fmt: skip
                repo.set_trusted(device_id, True)
                record_event(
                    s, AuditEvent.DEVICE_CONNECTED, f"{name} connected.", device_id=device_id
                )
            self._peers[device_id] = ActivePeer(device_id, name, address, port, public_key)
        self.publish_if_changed()
        return {
            "connection_id": f"conn_{uuid.uuid4().hex[:12]}",
            "device_id": self.core.identity.device_id,
            "device_name": self.core.device_name,
            "public_key": self.core.identity.public_key_b64,
            "platform": PLATFORM,
            "status": "connected",
        }

    def allow_inbound_transfer(self, device_id: str) -> None:
        """A trusted device may send if it is connected, or if a slot is free (or reclaimable)."""
        try:
            self._allow_inbound_transfer(device_id)
        finally:
            self.publish_if_changed()

    def _allow_inbound_transfer(self, device_id: str) -> None:
        with self._lock:
            held = self._peers.get(device_id)
            if held:
                held.status, held.fail_count = "connected", 0
                return
            with self.core.db.session() as s:  # read only: no session may nest inside a writer
                row = DeviceRepository(s).get_by_device_id(device_id)
                if row is None or not row.is_trusted or not row.public_key:
                    raise HandOffError("DEVICE_NOT_TRUSTED", "Device is not trusted.")
                known = ActivePeer(
                    device_id, row.device_name, row.last_ip, row.port, row.public_key
                )
            self._admit(device_id)
            with self.core.db.write_session() as s:
                if row := DeviceRepository(s).get_by_device_id(device_id):
                    row.status = "connected"
            self._peers[device_id] = known

    def release_inbound(self, device_id: str) -> None:
        with self._lock:
            self._peers.pop(device_id, None)
        self.publish_if_changed()
        with self.core.db.write_session() as s:
            row = DeviceRepository(s).get_by_device_id(device_id)
            if row:
                row.status = "available"

    # ----- health / offline detection -----------------------------------------------------

    def _probe(self, peer: ActivePeer) -> bool:
        address, port = peer.endpoint()
        try:
            resp = self.client.request(
                address, port, "GET", "/api/v1/health",
                expected_key=peer.public_key, signed=False, timeout=3.0,
            )  # fmt: skip
            return resp.status_code == 200 and resp.json().get("device_id") == peer.device_id
        except (HandOffError, ValueError):
            return False

    def _tick(self) -> None:
        """Probe every peer at once (a dead one must not delay the others) and apply the results."""
        with self._lock:
            snapshots = []
            for peer in self._peers.values():
                found = self.discovery.get(peer.device_id)
                if found and (found.address, found.port) != (peer.address, peer.port):
                    peer.address, peer.port = found.address, found.port  # IP/port changed
                if peer.address and peer.port:
                    snapshots.append(ActivePeer(**{**peer.__dict__}))
        if not snapshots:
            return
        results = list(self._prober.map(self._probe, snapshots))
        went_offline: list[str] = []
        with self._lock:
            for snap, ok in zip(snapshots, results, strict=True):
                a = self._peers.get(snap.device_id)
                if a is None:  # released while we were probing
                    continue
                if ok:
                    a.fail_count = 0
                    if a.status == "offline":
                        a.status = "connected"
                        self._record_status(
                            a, "connected", AuditEvent.DEVICE_CONNECTED, "is back online"
                        )
                else:
                    a.fail_count += 1
                    if a.status == "connected" and a.fail_count >= self._threshold:
                        a.status = "offline"
                        self._record_status(a, "offline", AuditEvent.DEVICE_OFFLINE, "went offline")
                        went_offline.append(a.device_id)
        self.publish_if_changed()
        callback = self.on_offline
        if callback:
            for device_id in went_offline:
                callback(device_id)

    def _record_status(self, a: ActivePeer, status: str, event: AuditEvent, what: str) -> None:
        with self.core.db.write_session() as s:
            row = DeviceRepository(s).get_by_device_id(a.device_id)
            if row:
                row.status = status
            record_event(s, event, f"{a.device_name} {what}.", device_id=a.device_id)

"""Single-peer connection management (ADR-046, ADR-047, FR-011..FR-016).

Exactly one peer is active. Picking another device performs an internal graceful
disconnect first and is refused while a transfer is running. A peer that stops answering
health checks is marked offline (and any running upload is aborted); when it answers again it
is restored without a new handshake because trust is persistent.
"""

from __future__ import annotations

import logging
import platform as _platform
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from handoff.audit import AuditEvent, record_event
from handoff.config import HEALTH_FAILURE_THRESHOLD, HEALTH_INTERVAL_SECONDS
from handoff.core import Core
from handoff.db.repositories import DeviceRepository, TransferRepository
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
        self._active: ActivePeer | None = None
        self._monitor: PeriodicTask | None = None
        self.on_offline: Callable[[], None] | None = None

    # ----- lifecycle ---------------------------------------------------------------------

    def start(self) -> None:
        self._monitor = PeriodicTask(self._tick, self._interval, "peer-health")
        self._monitor.start()

    def stop(self) -> None:
        if self._monitor:
            self._monitor.stop()

    # ----- state --------------------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            a = self._active
            return {
                "connected": bool(a and a.status == "connected"),
                "device": a.as_dict() if a else None,
            }

    def active_peer(self) -> ActivePeer | None:
        with self._lock:
            return self._active

    def require_connected(self, device_id: str) -> ActivePeer:
        with self._lock:
            a = self._active
            if a is None or a.device_id != device_id:
                raise HandOffError("DEVICE_NOT_FOUND", "That device is not the connected device.")
            if a.status != "connected" or not a.address or not a.port:
                raise HandOffError("DEVICE_OFFLINE", f"{a.device_name} is offline.")
            return a

    # ----- listing ------------------------------------------------------------------------

    def discover(self) -> list[dict[str, Any]]:
        with self.core.db.session() as s:
            known = {d.device_id: d for d in DeviceRepository(s).list_all()}
        with self._lock:
            active = self._active
        out = []
        for p in self.discovery.peers():
            status = "available"
            if active and active.device_id == p.device_id:
                status = active.status
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
        with self._lock:
            a = self._active
        for d in devices:
            if a and a.device_id == d["device_id"]:
                d["status"] = a.status
        return devices

    # ----- outbound connect (user clicked Connect) ----------------------------------------

    def connect(self, device_id: str) -> dict[str, Any]:
        with self._lock:
            peer = self.discovery.get(device_id)
            if peer is None:
                raise HandOffError("DEVICE_NOT_FOUND", "That device is no longer visible.")
            active = self._active
            if active and active.device_id == device_id and active.status == "connected":
                return self.snapshot()
            if active and active.device_id != device_id and active.status == "connected":
                self._ensure_no_active_transfer()
                self._release_outbound(active)
            self._active = None

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

            with self.core.db.session() as s:
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
            self._active = ActivePeer(device_id, name, peer.address, peer.port, expected)
            return self.snapshot()

    def _ensure_no_active_transfer(self) -> None:
        with self.core.db.session() as s:
            if TransferRepository(s).get_active() is not None:
                raise HandOffError(
                    "INVALID_STATE", "A transfer is in progress. Wait until it finishes to switch."
                )

    def _release_outbound(self, active: ActivePeer) -> None:
        """Graceful internal disconnect (ADR-047). Best effort: the switch proceeds regardless."""
        if not active.address or not active.port:
            return
        try:
            self.client.request(
                active.address,
                active.port,
                "DELETE",
                "/api/v1/connection",
                expected_key=active.public_key,
            )
        except HandOffError as exc:
            log.warning("Could not notify %s of disconnect: %s", active.device_name, exc.code)
        with self.core.db.session() as s:
            row = DeviceRepository(s).get_by_device_id(active.device_id)
            if row:
                row.status = "available"

    def _audit_tls_failure(self, device_id: str, message: str) -> None:
        with self.core.db.session() as s:
            record_event(s, AuditEvent.TLS_FAILURE, message, device_id=device_id)

    # ----- inbound (a peer called us) -----------------------------------------------------

    def accept_inbound(
        self, device_id: str, name: str, public_key: str, address: str, port: int, platform: str
    ) -> dict[str, Any]:
        """POST /connection: the connecting user's click makes the device trusted (FR-011)."""
        with self._lock:
            active = self._active
            if active and active.device_id != device_id and active.status == "connected":
                raise HandOffError(
                    "DEVICE_ALREADY_CONNECTED",
                    "This device is already connected to another device.",
                )
            with self.core.db.session() as s:
                repo = DeviceRepository(s)
                repo.upsert(
                    device_id, name, platform, last_ip=address, port=port,
                    public_key=public_key, status="connected",
                )  # fmt: skip
                repo.set_trusted(device_id, True)
                record_event(
                    s, AuditEvent.DEVICE_CONNECTED, f"{name} connected.", device_id=device_id
                )
            self._active = ActivePeer(device_id, name, address, port, public_key)
        return {
            "connection_id": f"conn_{uuid.uuid4().hex[:12]}",
            "device_id": self.core.identity.device_id,
            "device_name": self.core.device_name,
            "public_key": self.core.identity.public_key_b64,
            "platform": PLATFORM,
            "status": "connected",
        }

    def allow_inbound_transfer(self, device_id: str) -> None:
        """A trusted device may send if it is the connected peer, or if no peer is connected."""
        with self._lock:
            active = self._active
            if active and active.device_id != device_id and active.status == "connected":
                raise HandOffError(
                    "DEVICE_ALREADY_CONNECTED", "This device is connected to another device."
                )
            if active and active.device_id == device_id:
                active.status, active.fail_count = "connected", 0
                return
            with self.core.db.session() as s:
                row = DeviceRepository(s).get_by_device_id(device_id)
                if row is None or not row.is_trusted or not row.public_key:
                    raise HandOffError("DEVICE_NOT_TRUSTED", "Device is not trusted.")
                self._active = ActivePeer(
                    device_id, row.device_name, row.last_ip, row.port, row.public_key
                )
                row.status = "connected"

    def release_inbound(self, device_id: str) -> None:
        with self._lock:
            if self._active and self._active.device_id == device_id:
                self._active = None
        with self.core.db.session() as s:
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
        with self._lock:
            active = self._active
            if active is None:
                return
            found = self.discovery.get(active.device_id)
            if found and (found.address, found.port) != (active.address, active.port):
                active.address, active.port = found.address, found.port  # IP/port changed
            if not active.address or not active.port:
                return
            snapshot = ActivePeer(**{**active.__dict__})
        ok = self._probe(snapshot)
        callback = None
        with self._lock:
            if self._active is None or self._active.device_id != snapshot.device_id:
                return
            a = self._active
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
                    callback = self.on_offline
        if callback:
            callback()

    def _record_status(self, a: ActivePeer, status: str, event: AuditEvent, what: str) -> None:
        with self.core.db.session() as s:
            row = DeviceRepository(s).get_by_device_id(a.device_id)
            if row:
                row.status = status
            record_event(s, event, f"{a.device_name} {what}.", device_id=a.device_id)

"""Wires the networking services around a Core (ARCHTECTURE §6, ADR-053)."""

from __future__ import annotations

import logging
import socket
from collections.abc import Callable
from dataclasses import dataclass

from handoff.config import (
    ACCEPT_TIMEOUT_SECONDS,
    API_VERSION,
    HEALTH_FAILURE_THRESHOLD,
    HEALTH_INTERVAL_SECONDS,
    PEER_PORT,
)
from handoff.core import Core
from handoff.devices.client import PeerClient
from handoff.devices.connection import ConnectionManager
from handoff.devices.discovery import (
    Advertisement,
    DiscoveredPeer,
    Discovery,
    ZeroconfDiscovery,
)
from handoff.history import PeriodicTask
from handoff.peer_api.app import PeerContext, create_app
from handoff.peer_api.auth import Authenticator
from handoff.peer_api.server import PeerServer
from handoff.peer_api.signing import FailureLimiter, NonceCache
from handoff.tls import write_server_tls_files
from handoff.transfer.drop import DropService
from handoff.transfer.receiver import ReceiverService
from handoff.transfer.sender import SenderService

log = logging.getLogger(__name__)

_EXPIRY_INTERVAL = 5.0


def detect_lan_ip() -> str:
    """The address other devices reach us on (no packets are sent). Falls back to loopback."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("192.0.2.1", 9))
            return str(s.getsockname()[0])
        except OSError:
            return "127.0.0.1"


@dataclass
class NetworkConfig:
    host: str | None = None  # None: the detected LAN address (never 0.0.0.0)
    port: int = PEER_PORT
    discovery: Discovery | None = None
    health_interval: float = HEALTH_INTERVAL_SECONDS
    failure_threshold: int = HEALTH_FAILURE_THRESHOLD
    accept_timeout: float = ACCEPT_TIMEOUT_SECONDS


class Network:
    def __init__(self, core: Core, config: NetworkConfig | None = None) -> None:
        self.core = core
        self.config = config or NetworkConfig()
        self.port = 0
        self.host = ""
        self.client: PeerClient
        self.discovery: Discovery
        self.connections: ConnectionManager
        self.receiver: ReceiverService
        self.sender: SenderService
        self.drops: DropService
        self._server: PeerServer | None = None
        self._expiry: PeriodicTask | None = None

    def start(self) -> None:
        try:
            self._start()
        except BaseException:
            self.stop()
            raise

    def _start(self) -> None:
        cfg, core = self.config, self.core
        self.host = cfg.host or detect_lan_ip()
        self.client = PeerClient(core.identity)
        self.discovery = cfg.discovery or ZeroconfDiscovery()
        limiter = FailureLimiter()
        self.connections = ConnectionManager(
            core, self.client, self.discovery, lambda: self.port,
            health_interval=cfg.health_interval, failure_threshold=cfg.failure_threshold,
        )  # fmt: skip
        self.receiver = ReceiverService(
            core, self.connections, limiter, accept_timeout=cfg.accept_timeout
        )
        self.sender = SenderService(core, self.connections, self.client)
        self.drops = DropService(core, self.connections, self.sender)
        self.drops.sweep_orphans()  # a crash can leave dropped copies behind
        self.connections.on_offline = lambda: self.sender.abort_active(
            "DEVICE_OFFLINE", "The device went offline."
        )
        app = create_app(
            PeerContext(
                core, self.connections, self.receiver, Authenticator(core, NonceCache(), limiter)
            )
        )
        cert, key = write_server_tls_files(core.paths, core.identity)
        self._server = PeerServer(app, self.host, cfg.port, cert, key)
        self.port = self._server.start()
        self.discovery.start(
            Advertisement(
                core.identity.device_id, core.device_name, core.identity.public_key_b64,
                self.port, self.host,
            )
        )  # fmt: skip
        self.connections.start()
        self._expiry = PeriodicTask(self.receiver.expire_stale, _EXPIRY_INTERVAL, "transfer-expiry")
        self._expiry.start()
        log.info("Peer API listening on https://%s:%s", self.host, self.port)

    def stop(self) -> None:
        """Stop everything that was started; one failing part must not stop the rest."""
        parts: list[tuple[str, Callable[[], None]]] = []
        if self._expiry:
            parts.append(("expiry", self._expiry.stop))
        for name in ("connections", "sender", "discovery"):
            part = getattr(self, name, None)
            if part is not None:
                parts.append((name, part.stop))
        if self._server:
            parts.append(("server", self._server.stop))
        for name, stop in parts:
            try:
                stop()
            except Exception:
                log.exception("Could not stop %s cleanly", name)

    def advertisement(self) -> DiscoveredPeer:
        """This device as another device's discovery would see it (used by tests and tools)."""
        return DiscoveredPeer(
            self.core.identity.device_id, self.core.device_name, self.host, self.port,
            API_VERSION, self.core.identity.public_key_b64,
        )  # fmt: skip

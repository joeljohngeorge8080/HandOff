"""LAN device discovery (API §7, SECURITY §13).

`ZeroconfDiscovery` advertises and browses `_handoff._tcp.local.` over mDNS.
`StaticDiscovery` is a drop-in with a fixed peer list, used by tests and tools.
Discovery data is *unauthenticated hints*: the advertised fingerprint is only an expectation
that the TLS handshake and signed requests must then confirm.
"""

from __future__ import annotations

import logging
import socket
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Protocol

from zeroconf import IPVersion, ServiceBrowser, ServiceInfo, Zeroconf

from handoff.config import API_VERSION, SERVICE_TYPE

log = logging.getLogger(__name__)

_MAX_NAME = 64


@dataclass(frozen=True)
class DiscoveredPeer:
    device_id: str
    device_name: str
    address: str
    port: int
    api_version: str
    fingerprint: str  # advertised public key (base64); an expectation, not proof


@dataclass(frozen=True)
class Advertisement:
    device_id: str
    device_name: str
    public_key: str
    port: int
    address: str


class Discovery(Protocol):
    def start(self, advertisement: Advertisement) -> None: ...
    def stop(self) -> None: ...
    def peers(self) -> list[DiscoveredPeer]: ...
    def get(self, device_id: str) -> DiscoveredPeer | None: ...


class StaticDiscovery:
    """Fixed, thread-safe peer list."""

    def __init__(self, peers: list[DiscoveredPeer] | None = None) -> None:
        self._lock = threading.Lock()
        self._peers = {p.device_id: p for p in peers or []}
        self.advertisement: Advertisement | None = None

    def start(self, advertisement: Advertisement) -> None:
        self.advertisement = advertisement

    def stop(self) -> None:
        return None

    def set_peers(self, peers: list[DiscoveredPeer]) -> None:
        with self._lock:
            self._peers = {p.device_id: p for p in peers}

    def peers(self) -> list[DiscoveredPeer]:
        with self._lock:
            return sorted(self._peers.values(), key=lambda p: p.device_name)

    def get(self, device_id: str) -> DiscoveredPeer | None:
        with self._lock:
            return self._peers.get(device_id)


def _parse_service(info: ServiceInfo, own_device_id: str) -> DiscoveredPeer | None:
    """Turn an mDNS record into a peer, ignoring anything malformed or our own record."""
    props = {
        (k.decode("utf-8", "replace") if isinstance(k, bytes) else str(k)): v
        for k, v in (info.properties or {}).items()
    }

    def text(key: str) -> str | None:
        raw = props.get(key)
        return raw.decode("utf-8", "replace") if isinstance(raw, bytes) else None

    device_id, name, fp = text("device_id"), text("device_name"), text("fp")
    try:
        if device_id is None or str(uuid.UUID(device_id)) != device_id:
            return None
    except ValueError:
        return None
    if device_id == own_device_id or not name or not fp:
        return None
    addresses = info.parsed_addresses(IPVersion.V4Only)
    port = info.port
    if not addresses or not isinstance(port, int) or not 0 < port < 65536:
        return None
    return DiscoveredPeer(
        device_id, name.strip()[:_MAX_NAME], addresses[0], port, text("api_version") or "", fp
    )


class ZeroconfDiscovery:
    def __init__(self, interfaces: list[str] | None = None) -> None:
        self._interfaces = interfaces
        self._zc: Zeroconf | None = None
        self._info: ServiceInfo | None = None
        self._browser: ServiceBrowser | None = None
        self._own_id = ""
        self._lock = threading.Lock()
        self._by_service: dict[str, DiscoveredPeer] = {}
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mdns-resolve")
        self._thread: threading.Thread | None = None

    # -- lifecycle ---------------------------------------------------------------------------

    def start(self, advertisement: Advertisement) -> None:
        """Registering probes the network for a few seconds, so it runs off the caller's thread."""
        self._own_id = advertisement.device_id
        self._thread = threading.Thread(
            target=self._run, args=(advertisement,), name="mdns-start", daemon=True
        )
        self._thread.start()

    def _run(self, ad: Advertisement) -> None:
        try:
            kwargs: dict[str, object] = {"ip_version": IPVersion.V4Only}
            if self._interfaces:
                kwargs["interfaces"] = self._interfaces
            zc = Zeroconf(**kwargs)  # type: ignore[arg-type]
            info = ServiceInfo(
                SERVICE_TYPE,
                f"{ad.device_id}.{SERVICE_TYPE}",
                addresses=[socket.inet_aton(ad.address)],
                port=ad.port,
                properties={
                    "device_id": ad.device_id,
                    "device_name": ad.device_name[:_MAX_NAME],
                    "api_port": str(ad.port),
                    "api_version": API_VERSION,
                    "fp": ad.public_key,
                },
            )
            zc.register_service(info)
            self._zc, self._info = zc, info
            self._browser = ServiceBrowser(zc, SERVICE_TYPE, handlers=[self._on_change])
        except Exception:
            log.exception("mDNS discovery could not start; devices will not be found automatically")

    def stop(self) -> None:
        if self._thread:
            self._thread.join(timeout=10)
        self._pool.shutdown(wait=False, cancel_futures=True)
        zc, info = self._zc, self._info
        self._zc = self._info = None
        if zc is not None:
            try:
                if info is not None:
                    zc.unregister_service(info)
            finally:
                zc.close()

    # -- browsing ----------------------------------------------------------------------------

    def _on_change(
        self, zeroconf: Zeroconf, service_type: str, name: str, state_change: object
    ) -> None:
        if "Removed" in str(state_change):
            with self._lock:
                self._by_service.pop(name, None)
            return
        self._pool.submit(self._resolve, zeroconf, service_type, name)

    def _resolve(self, zeroconf: Zeroconf, service_type: str, name: str) -> None:
        try:
            info = zeroconf.get_service_info(service_type, name, timeout=3000)
            peer = _parse_service(info, self._own_id) if info else None
        except Exception:
            log.exception("Could not resolve mDNS service %s", name)
            return
        with self._lock:
            if peer is None:
                self._by_service.pop(name, None)
            else:
                self._by_service[name] = peer

    def peers(self) -> list[DiscoveredPeer]:
        with self._lock:
            return sorted(self._by_service.values(), key=lambda p: p.device_name)

    def get(self, device_id: str) -> DiscoveredPeer | None:
        with self._lock:
            return next((p for p in self._by_service.values() if p.device_id == device_id), None)

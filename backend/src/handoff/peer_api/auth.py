"""Peer request authentication (SECURITY §35, API §43.1).

Checks, in order: rate limit -> headers -> device-id shape -> timestamp window ->
key resolution (trusted device, or the key in the body of POST /connection) -> Ed25519
signature -> nonce replay. Any failure is rejected and written to the audit log.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from handoff.audit import AuditEvent, record_event
from handoff.core import Core
from handoff.db.repositories import DeviceRepository
from handoff.errors import HandOffError
from handoff.peer_api.signing import (
    FailureLimiter,
    NonceCache,
    canonical_string,
    parse_timestamp,
    timestamp_is_fresh,
    transfer_id_from_path,
    verify_signature,
)
from handoff.tls import keys_equal

_NONCE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


@dataclass(frozen=True)
class PeerIdentity:
    device_id: str
    public_key: str


def _canonical_uuid(value: str) -> str | None:
    try:
        parsed = str(uuid.UUID(value))
    except (ValueError, AttributeError):
        return None
    return parsed if parsed == value else None


class Authenticator:
    def __init__(
        self,
        core: Core,
        nonces: NonceCache,
        limiter: FailureLimiter,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.core, self.nonces, self.limiter, self.clock = core, nonces, limiter, clock

    def _reject(
        self, client_ip: str, device_id: str | None, code: str, message: str
    ) -> HandOffError:
        crossed = self.limiter.record(client_ip)
        with self.core.db.session() as s:
            record_event(
                s,
                AuditEvent.INVALID_DEVICE,
                f"Rejected peer request: {message}",
                device_id=device_id,
                metadata={"code": code, "client": client_ip[:64]},
            )
            if crossed:
                record_event(
                    s,
                    AuditEvent.INVALID_DEVICE,
                    "Too many invalid requests; further ones are blocked for a while.",
                    metadata={"client": client_ip[:64]},
                )
        return HandOffError(code, message)

    def authenticate(
        self,
        *,
        method: str,
        path: str,
        headers: Mapping[str, str],
        client_ip: str,
        claimed_key: str | None = None,
        require_trusted: bool = True,
    ) -> PeerIdentity:
        if self.limiter.blocked(client_ip):
            raise HandOffError("RATE_LIMITED", "Too many invalid requests. Try again later.")
        raw_id = headers.get("x-device-id")
        timestamp = headers.get("x-timestamp")
        nonce = headers.get("x-nonce")
        signature = headers.get("x-signature")
        if not (raw_id and timestamp and nonce and signature):
            raise self._reject(
                client_ip, None, "INVALID_SIGNATURE", "Missing authentication headers."
            )
        device_id = _canonical_uuid(raw_id)
        if device_id is None:
            raise self._reject(client_ip, None, "INVALID_DEVICE_ID", "Invalid device ID.")
        if not _NONCE.match(nonce):
            raise self._reject(client_ip, device_id, "INVALID_SIGNATURE", "Invalid nonce.")
        parsed = parse_timestamp(timestamp)
        if parsed is None or not timestamp_is_fresh(parsed, self.clock()):
            raise self._reject(
                client_ip,
                device_id,
                "INVALID_SIGNATURE",
                "Timestamp is invalid or outside the window.",
            )

        with self.core.db.session() as s:
            row = DeviceRepository(s).get_by_device_id(device_id)
            known_key = row.public_key if row else None
            trusted = bool(row and row.is_trusted)
        if claimed_key is not None:
            if known_key and not keys_equal(known_key, claimed_key):
                raise self._reject(
                    client_ip, device_id, "DEVICE_NOT_TRUSTED", "Device identity does not match."
                )
            key = claimed_key
        else:
            if require_trusted and not (trusted and known_key):
                raise self._reject(
                    client_ip, device_id, "DEVICE_NOT_TRUSTED", "Device is not trusted."
                )
            if not known_key:
                raise self._reject(
                    client_ip, device_id, "DEVICE_NOT_TRUSTED", "Device is not known."
                )
            key = known_key

        message = canonical_string(
            method, path, device_id, timestamp, nonce, transfer_id_from_path(path)
        )
        if not verify_signature(key, message, signature):
            raise self._reject(client_ip, device_id, "INVALID_SIGNATURE", "Signature is invalid.")
        if not self.nonces.check_and_store(device_id, nonce):
            raise self._reject(
                client_ip, device_id, "REPLAYED_REQUEST", "Request was already used."
            )
        return PeerIdentity(device_id, key)

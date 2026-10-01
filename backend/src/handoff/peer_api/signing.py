"""Signed peer requests, replay protection and failure limiting (API §43.1, SECURITY §35-38).

Signed string: METHOD|PATH|device_id|timestamp|nonce|transfer_id
"""

from __future__ import annotations

import base64
import binascii
import re
import secrets
import threading
import time
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from handoff.config import (
    FAILURE_LIMIT,
    FAILURE_WINDOW_SECONDS,
    NONCE_TTL_SECONDS,
    SIGNATURE_MAX_SKEW_SECONDS,
)
from handoff.identity import Identity

_TRANSFER_PATH = re.compile(r"^/api/v1/transfers/([^/]+)(?:/|$)")
_TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def transfer_id_from_path(path: str) -> str:
    m = _TRANSFER_PATH.match(path)
    return m.group(1) if m else ""


def canonical_string(
    method: str, path: str, device_id: str, timestamp: str, nonce: str, transfer_id: str
) -> bytes:
    return "|".join((method.upper(), path, device_id, timestamp, nonce, transfer_id)).encode()


def format_timestamp(when: datetime) -> str:
    return when.astimezone(UTC).strftime(_TIME_FORMAT)


def parse_timestamp(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, _TIME_FORMAT).replace(tzinfo=UTC)
    except ValueError:
        return None


def sign_request(
    identity: Identity,
    method: str,
    path: str,
    *,
    now: datetime | None = None,
    nonce: str | None = None,
) -> dict[str, str]:
    timestamp = format_timestamp(now or datetime.now(UTC))
    nonce = nonce or secrets.token_urlsafe(18)
    message = canonical_string(
        method, path, identity.device_id, timestamp, nonce, transfer_id_from_path(path)
    )
    return {
        "X-Device-ID": identity.device_id,
        "X-Timestamp": timestamp,
        "X-Nonce": nonce,
        "X-Signature": base64.b64encode(identity.private_key.sign(message)).decode("ascii"),
    }


def verify_signature(public_key_b64: str, message: bytes, signature_b64: str) -> bool:
    try:
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_b64, validate=True))
        key.verify(base64.b64decode(signature_b64, validate=True), message)
    except (InvalidSignature, ValueError, binascii.Error):
        return False
    return True


def timestamp_is_fresh(ts: datetime, now: datetime) -> bool:
    return abs((now - ts).total_seconds()) <= SIGNATURE_MAX_SKEW_SECONDS


class NonceCache:
    """Remembers (device, nonce) pairs for a bit longer than the allowed clock skew."""

    def __init__(self, ttl: float = NONCE_TTL_SECONDS, clock: Callable[[], float] = time.monotonic):
        self._ttl, self._clock = ttl, clock
        self._seen: dict[tuple[str, str], float] = {}
        self._lock = threading.Lock()

    def check_and_store(self, device_id: str, nonce: str) -> bool:
        """True if this nonce is new. False means a replay."""
        now = self._clock()
        with self._lock:
            for key in [k for k, exp in self._seen.items() if exp <= now]:
                del self._seen[key]
            if (device_id, nonce) in self._seen:
                return False
            self._seen[(device_id, nonce)] = now + self._ttl
            return True


class FailureLimiter:
    """Sliding-window counter of invalid requests per key (device ID or client address)."""

    def __init__(
        self,
        limit: int = FAILURE_LIMIT,
        window: float = FAILURE_WINDOW_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._limit, self._window, self._clock = limit, window, clock
        self._events: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> deque[float]:
        q = self._events.setdefault(key, deque())
        while q and q[0] <= now - self._window:
            q.popleft()
        return q

    def blocked(self, key: str) -> bool:
        with self._lock:
            return len(self._prune(key, self._clock())) >= self._limit

    def record(self, key: str) -> bool:
        """Record a failure. True exactly once: when the limit is first reached."""
        now = self._clock()
        with self._lock:
            q = self._prune(key, now)
            q.append(now)
            return len(q) == self._limit

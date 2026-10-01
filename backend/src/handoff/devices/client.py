"""Outgoing peer requests: pinned TLS + signed headers (ADR-053).

Before any request reaches a peer, `pin()` proves that the TLS certificate on the other end
belongs to the device key we expect. Only then is that certificate trusted for the actual
request, so file data is never sent to an unverified endpoint.
"""

from __future__ import annotations

import ssl
import threading
import uuid
from typing import Any

import httpx

from handoff.config import CONNECT_TIMEOUT_SECONDS, REQUEST_TIMEOUT_SECONDS
from handoff.errors import HandOffError
from handoff.identity import Identity
from handoff.peer_api.signing import sign_request
from handoff.tls import (
    fetch_server_cert_der,
    keys_equal,
    pinned_context,
    public_key_b64_from_cert_der,
)


class PinMismatchError(HandOffError):
    """The device at this address is not the device we expected (possible impersonation)."""

    def __init__(self) -> None:
        super().__init__(
            "PEER_CONNECTION_FAILED",
            "The device could not be verified: its identity does not match.",
            {"reason": "identity_mismatch"},
        )


def translate_transport_error(exc: Exception) -> HandOffError:
    if isinstance(exc, httpx.ConnectError | httpx.ConnectTimeout):
        return HandOffError("PEER_CONNECTION_FAILED", "The device could not be reached.")
    if isinstance(exc, httpx.TimeoutException):
        return HandOffError("REQUEST_TIMEOUT", "The device did not respond in time.")
    if isinstance(exc, httpx.RemoteProtocolError | httpx.ReadError | httpx.WriteError):
        return HandOffError("CONNECTION_RESET", "The connection to the device was lost.")
    return HandOffError("NETWORK_ERROR", "A network error occurred.")


def error_from_response(resp: httpx.Response) -> HandOffError:
    """Rebuild the peer's standard error (API §32), tolerating anything unexpected."""
    try:
        err = resp.json()["error"]
        return HandOffError(
            str(err["code"])[:64],
            str(err.get("message", "The device rejected the request."))[:300],
            err.get("details") if isinstance(err.get("details"), dict) else None,
        )
    except (ValueError, KeyError, TypeError):
        return HandOffError(
            "NETWORK_ERROR", f"Unexpected response from the device ({resp.status_code})."
        )


def _is_cert_error(exc: BaseException | None) -> bool:
    while exc is not None:
        if isinstance(exc, ssl.SSLCertVerificationError):
            return True
        exc = exc.__cause__ or exc.__context__
    return False


class PeerClient:
    def __init__(self, identity: Identity) -> None:
        self.identity = identity
        self._anchors: dict[tuple[str, int], tuple[str, ssl.SSLContext]] = {}
        self._lock = threading.Lock()

    def pin(self, address: str, port: int, expected_key_b64: str) -> None:
        try:
            der = fetch_server_cert_der(address, port, CONNECT_TIMEOUT_SECONDS)
        except (OSError, ssl.SSLError) as exc:
            raise HandOffError(
                "PEER_CONNECTION_FAILED", "The device could not be reached."
            ) from exc
        if not keys_equal(public_key_b64_from_cert_der(der), expected_key_b64):
            raise PinMismatchError
        with self._lock:
            self._anchors[(address, port)] = (expected_key_b64, pinned_context(der))

    def forget(self, address: str, port: int) -> None:
        with self._lock:
            self._anchors.pop((address, port), None)

    def _context(self, address: str, port: int, expected_key: str) -> ssl.SSLContext:
        with self._lock:
            anchor = self._anchors.get((address, port))
        if anchor is None or not keys_equal(anchor[0], expected_key):
            self.pin(address, port, expected_key)
            with self._lock:
                anchor = self._anchors[(address, port)]
        return anchor[1]

    def request(
        self,
        address: str,
        port: int,
        method: str,
        path: str,
        *,
        expected_key: str,
        json: Any = None,
        content: Any = None,
        headers: dict[str, str] | None = None,
        signed: bool = True,
        timeout: httpx.Timeout | float | None = None,
    ) -> httpx.Response:
        ctx = self._context(address, port, expected_key)
        for attempt in (1, 2):
            hdrs = {"X-Request-ID": f"req_{uuid.uuid4().hex[:16]}", **(headers or {})}
            if signed:
                hdrs.update(sign_request(self.identity, method, path))
            try:
                with httpx.Client(
                    verify=ctx, timeout=timeout or REQUEST_TIMEOUT_SECONDS, follow_redirects=False
                ) as client:
                    return client.request(
                        method,
                        f"https://{address}:{port}{path}",
                        json=json,
                        content=content,
                        headers=hdrs,
                    )
            except httpx.ConnectError as exc:
                # The peer may have restarted with a fresh certificate for the same key.
                if attempt == 1 and _is_cert_error(exc):
                    self.forget(address, port)
                    ctx = self._context(address, port, expected_key)
                    continue
                raise translate_transport_error(exc) from exc
            except httpx.HTTPError as exc:
                raise translate_transport_error(exc) from exc
        raise AssertionError("unreachable")  # pragma: no cover

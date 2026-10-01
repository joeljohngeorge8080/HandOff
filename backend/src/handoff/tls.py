"""TLS identity and certificate pinning (SECURITY §16-18, ADR-053).

Each install signs its own TLS certificate with its Ed25519 identity key, so the pinned
"public key" of a peer *is* its device identity. Peers never trust public CAs. A client
first fetches the server's certificate (without trusting it), checks that its public key is
the expected device key, and only then uses that exact certificate as the sole trust anchor
for real requests. Everything is TLS 1.3.
"""

from __future__ import annotations

import base64
import contextlib
import hmac
import os
import socket
import ssl
from datetime import UTC, datetime, timedelta

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.x509.oid import NameOID

from handoff.config import CONNECT_TIMEOUT_SECONDS
from handoff.errors import HandOffError
from handoff.identity import Identity
from handoff.paths import AppPaths

_CERT_FILE = "tls-cert.pem"
_KEY_FILE = "tls-key.pem"
_VALID_DAYS = 3650


def build_self_signed_cert(identity: Identity, now: datetime | None = None) -> bytes:
    now = now or datetime.now(UTC)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"handoff-{identity.device_id}")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(identity.private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=_VALID_DAYS))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(identity.private_key, algorithm=None)
    )
    return cert.public_bytes(serialization.Encoding.PEM)


def write_server_tls_files(paths: AppPaths, identity: Identity) -> tuple[str, str]:
    """(Re)write the cert/key pair the HTTPS server loads. Owner-only; the key is the identity."""
    cert_path, key_path = paths.keys_dir / _CERT_FILE, paths.keys_dir / _KEY_FILE
    key_pem = identity.private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    for path, data in ((cert_path, build_self_signed_cert(identity)), (key_path, key_pem)):
        tmp = path.with_suffix(".tmp")
        with contextlib.suppress(FileNotFoundError):
            tmp.unlink()
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    return str(cert_path), str(key_path)


def public_key_b64_from_cert_der(der: bytes) -> str:
    """Raw Ed25519 public key (base64) of a DER certificate; anything else is rejected."""
    try:
        cert = x509.load_der_x509_certificate(der)
        key = cert.public_key()
    except ValueError as exc:
        raise HandOffError(
            "PEER_CONNECTION_FAILED", "The device sent an invalid certificate."
        ) from exc
    if not isinstance(key, Ed25519PublicKey):
        raise HandOffError(
            "PEER_CONNECTION_FAILED", "The device certificate has the wrong key type."
        )
    raw = key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(raw).decode("ascii")


def keys_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("ascii", "replace"), b.encode("ascii", "replace"))


def fingerprint(public_key_b64: str) -> str:
    """Short human-comparable fingerprint (for logs/UI), never used for trust decisions."""
    digest = hashes.Hash(hashes.SHA256())
    digest.update(base64.b64decode(public_key_b64))
    return digest.finalize().hex()[:16]


def _client_context() -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_3
    ctx.check_hostname = False  # identity is the pinned key, not a DNS name
    return ctx


def fetch_server_cert_der(host: str, port: int, timeout: float = CONNECT_TIMEOUT_SECONDS) -> bytes:
    """Complete a TLS handshake *without trusting the server*, only to read its certificate.

    The TLS 1.3 handshake still proves the server holds the private key for that
    certificate. No application data is sent.
    """
    ctx = _client_context()
    ctx.verify_mode = ssl.CERT_NONE
    with (
        socket.create_connection((host, port), timeout=timeout) as sock,
        ctx.wrap_socket(sock) as tls,
    ):
        der = tls.getpeercert(binary_form=True)
    if not der:
        raise HandOffError("PEER_CONNECTION_FAILED", "The device did not present a certificate.")
    return der


def pinned_context(cert_der: bytes) -> ssl.SSLContext:
    """Trust exactly one certificate: the one whose key was already verified as the device's."""
    ctx = _client_context()
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.load_verify_locations(cadata=ssl.DER_cert_to_PEM_cert(cert_der))
    return ctx

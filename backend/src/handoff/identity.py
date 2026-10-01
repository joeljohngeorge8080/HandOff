"""Persistent device identity (ADR-008, ADR-048, SECURITY §6-8).

One file holds the device_id and the Ed25519 private key, written atomically with
owner-only permissions. The private key never goes into the database, logs, API
responses or transfer payloads. A damaged identity file is an error: silently
regenerating it would change the device's identity.
"""

from __future__ import annotations

import base64
import contextlib
import json
import logging
import os
import uuid
from dataclasses import dataclass, field

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from handoff.errors import IdentityError
from handoff.paths import AppPaths

log = logging.getLogger(__name__)

_FILE = "identity.json"
_VERSION = 1


@dataclass(frozen=True)
class Identity:
    device_id: str
    private_key: Ed25519PrivateKey = field(repr=False, compare=False)

    @property
    def public_key_b64(self) -> str:
        raw = self.private_key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        return base64.b64encode(raw).decode("ascii")


def _serialize(identity: Identity) -> str:
    pem = identity.private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")
    return json.dumps({"version": _VERSION, "device_id": identity.device_id, "private_key": pem})


def _write_private(path_str: str, data: str) -> None:
    """Atomic write; the temp file is created 0600 so the key is never briefly readable."""
    tmp = f"{path_str}.tmp"
    with contextlib.suppress(FileNotFoundError):
        os.unlink(tmp)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path_str)


def load_or_create_identity(paths: AppPaths) -> Identity:
    path = paths.keys_dir / _FILE
    if not path.exists():
        identity = Identity(device_id=str(uuid.uuid4()), private_key=Ed25519PrivateKey.generate())
        _write_private(str(path), _serialize(identity))
        log.info("Generated new device identity %s", identity.device_id)
        return identity

    if os.name == "posix" and path.stat().st_mode & 0o077:
        log.warning("Identity file had loose permissions; restricting to owner only.")
        path.chmod(0o600)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        raw_id, raw_key = data["device_id"], data["private_key"]
        if not isinstance(raw_id, str) or not isinstance(raw_key, str):
            raise TypeError("identity fields must be strings")
        device_id = str(uuid.UUID(raw_id))
        key = serialization.load_pem_private_key(raw_key.encode("ascii"), password=None)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise IdentityError(
            "The device identity file is damaged. HandOff will not replace it automatically."
        ) from exc
    if not isinstance(key, Ed25519PrivateKey):
        raise IdentityError("The device identity file does not contain an Ed25519 key.")
    return Identity(device_id=device_id, private_key=key)

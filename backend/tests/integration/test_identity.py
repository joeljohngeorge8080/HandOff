import json
import os
import stat

import pytest

from handoff.core import Core
from handoff.errors import IdentityError
from handoff.identity import load_or_create_identity
from handoff.paths import AppPaths


def _restart(paths):
    c = Core(paths, hostname="Test-Laptop")
    c.start()
    return c


def test_device_identity_survives_restarts(paths):
    c1 = _restart(paths)
    first = (c1.identity.device_id, c1.identity.public_key_b64)
    c1.close()
    c2 = _restart(paths)
    assert (c2.identity.device_id, c2.identity.public_key_b64) == first
    c2.close()
    c3 = _restart(paths)
    assert c3.identity.device_id == first[0]
    c3.close()


def test_two_installations_get_different_identities(tmp_path):
    a = load_or_create_identity(_ensured(AppPaths(tmp_path / "a")))
    b = load_or_create_identity(_ensured(AppPaths(tmp_path / "b")))
    assert a.device_id != b.device_id
    assert a.public_key_b64 != b.public_key_b64


def _ensured(p):
    p.ensure()
    return p


@pytest.mark.skipif(os.name != "posix", reason="POSIX permissions")
def test_private_key_file_is_owner_only(paths):
    _restart(paths).close()
    f = paths.keys_dir / "identity.json"
    assert stat.S_IMODE(f.stat().st_mode) == 0o600
    assert stat.S_IMODE(paths.keys_dir.stat().st_mode) == 0o700


@pytest.mark.skipif(os.name != "posix", reason="POSIX permissions")
def test_loose_permissions_are_tightened_on_load(paths):
    _restart(paths).close()
    f = paths.keys_dir / "identity.json"
    f.chmod(0o644)
    _restart(paths).close()
    assert stat.S_IMODE(f.stat().st_mode) == 0o600


def test_private_key_never_reaches_the_database_repr_or_ipc(core):
    from handoff.ipc.dispatcher import Dispatcher

    d = Dispatcher(core)
    pem_marker = b"PRIVATE KEY"
    assert pem_marker not in core.paths.db_path.read_bytes()
    assert "private" not in repr(core.identity).lower()
    for action in ("status.snapshot", "settings.get", "files.list", "history.list"):
        out = d.handle_line(json.dumps({"id": 1, "action": action}))
        assert "PRIVATE" not in out.upper()
        assert core.identity.public_key_b64 not in out or action == "status.snapshot"
    # the on-disk identity file is the only place the key lives
    assert pem_marker in (core.paths.keys_dir / "identity.json").read_bytes()


@pytest.mark.parametrize(
    "content",
    ["", "not json", "{}", '{"device_id": "x", "private_key": "y"}', '{"device_id": 5}'],
)
def test_damaged_identity_is_an_error_and_is_never_regenerated(paths, content):
    _restart(paths).close()
    f = paths.keys_dir / "identity.json"
    f.write_text(content)
    with pytest.raises(IdentityError):
        _restart(paths)
    assert f.read_text() == content  # untouched


def test_non_ed25519_key_is_rejected(paths):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    _restart(paths).close()
    f = paths.keys_dir / "identity.json"
    data = json.loads(f.read_text())
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    data["private_key"] = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    f.write_text(json.dumps(data))
    with pytest.raises(IdentityError):
        _restart(paths)


def test_a_new_identity_is_written_atomically(paths):
    _restart(paths).close()
    assert [p.name for p in paths.keys_dir.iterdir()] == ["identity.json"]  # no .tmp left

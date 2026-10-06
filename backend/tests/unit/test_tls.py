import base64
import os
import stat
from datetime import UTC, datetime, timedelta

import pytest
from cryptography import x509
from helpers import new_identity

from handoff import tls
from handoff.errors import HandOffError
from handoff.paths import AppPaths


@pytest.fixture
def ident(tmp_path):
    return new_identity(tmp_path, "a")


def test_certificate_is_signed_with_the_device_key(ident):
    cert = x509.load_pem_x509_certificate(tls.build_self_signed_cert(ident))
    der = cert.public_bytes(__import__("cryptography").hazmat.primitives.serialization.Encoding.DER)
    assert tls.public_key_b64_from_cert_der(der) == ident.public_key_b64
    assert cert.subject == cert.issuer  # self-signed
    cert.verify_directly_issued_by(cert)
    assert ident.device_id in cert.subject.rfc4514_string()


def test_certificate_is_currently_valid_and_long_lived(ident):
    cert = x509.load_pem_x509_certificate(tls.build_self_signed_cert(ident))
    now = datetime.now(UTC)
    assert cert.not_valid_before_utc < now < cert.not_valid_after_utc
    assert cert.not_valid_after_utc - now > timedelta(days=365)


def test_garbage_and_wrong_key_type_certificates_are_rejected():
    with pytest.raises(HandOffError) as e:
        tls.public_key_b64_from_cert_der(b"not a cert")
    assert e.value.code == "PEER_CONNECTION_FAILED"

    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(x509.oid.NameOID.COMMON_NAME, "x")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name)
        .public_key(key.public_key()).serial_number(1)
        .not_valid_before(now).not_valid_after(now + timedelta(days=1))
        .sign(key, hashes.SHA256())
    )  # fmt: skip
    with pytest.raises(HandOffError):
        tls.public_key_b64_from_cert_der(cert.public_bytes(serialization.Encoding.DER))


def test_keys_equal_is_exact():
    a = base64.b64encode(b"a" * 32).decode()
    b = base64.b64encode(b"b" * 32).decode()
    assert tls.keys_equal(a, a) and not tls.keys_equal(a, b) and not tls.keys_equal(a, "")


def test_fingerprint_is_short_and_stable(ident):
    assert tls.fingerprint(ident.public_key_b64) == tls.fingerprint(ident.public_key_b64)
    assert len(tls.fingerprint(ident.public_key_b64)) == 16


@pytest.mark.skipif(os.name != "posix", reason="POSIX permissions")
def test_server_key_material_is_owner_only_and_rewritten_atomically(tmp_path, ident):
    paths = AppPaths(tmp_path / "x")
    paths.ensure()
    cert, key = tls.write_server_tls_files(paths, ident)
    tls.write_server_tls_files(paths, ident)  # a second start overwrites cleanly
    for f in (cert, key):
        assert stat.S_IMODE(os.stat(f).st_mode) == 0o600
    assert sorted(p.name for p in paths.keys_dir.iterdir()) == ["tls-cert.pem", "tls-key.pem"]

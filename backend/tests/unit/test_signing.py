from datetime import UTC, datetime, timedelta

import pytest
from helpers import new_identity

from handoff.config import SIGNATURE_MAX_SKEW_SECONDS
from handoff.peer_api.signing import (
    FailureLimiter,
    NonceCache,
    canonical_string,
    format_timestamp,
    parse_timestamp,
    sign_request,
    timestamp_is_fresh,
    transfer_id_from_path,
    verify_signature,
)


@pytest.fixture
def ident(tmp_path):
    return new_identity(tmp_path, "a")


def test_canonical_string_has_the_documented_shape():
    msg = canonical_string("post", "/api/v1/x", "dev", "2026-10-01T10:00:00Z", "n0nce", "tr_1")
    assert msg == b"POST|/api/v1/x|dev|2026-10-01T10:00:00Z|n0nce|tr_1"


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/api/v1/transfers/tr_1", "tr_1"),
        ("/api/v1/transfers/tr_1/data", "tr_1"),
        ("/api/v1/transfers", ""),
        ("/api/v1/device", ""),
        ("/api/v1/transfers/", ""),
    ],
)
def test_transfer_id_is_taken_from_the_path(path, expected):
    assert transfer_id_from_path(path) == expected


def test_signature_verifies_only_for_the_exact_request(ident):
    now = datetime(2026, 10, 1, 10, 0, 0, tzinfo=UTC)
    h = sign_request(ident, "GET", "/api/v1/device", now=now, nonce="abcdefgh12345")
    msg = canonical_string(
        "GET", "/api/v1/device", ident.device_id, h["X-Timestamp"], h["X-Nonce"], ""
    )
    assert verify_signature(ident.public_key_b64, msg, h["X-Signature"])
    for tampered in (
        canonical_string(
            "POST", "/api/v1/device", ident.device_id, h["X-Timestamp"], h["X-Nonce"], ""
        ),
        canonical_string(
            "GET", "/api/v1/other", ident.device_id, h["X-Timestamp"], h["X-Nonce"], ""
        ),
        canonical_string(
            "GET", "/api/v1/device", "someone-else", h["X-Timestamp"], h["X-Nonce"], ""
        ),
        canonical_string(
            "GET", "/api/v1/device", ident.device_id, "2026-10-01T10:00:01Z", h["X-Nonce"], ""
        ),
        canonical_string(
            "GET", "/api/v1/device", ident.device_id, h["X-Timestamp"], "other-nonce", ""
        ),
        canonical_string(
            "GET", "/api/v1/device", ident.device_id, h["X-Timestamp"], h["X-Nonce"], "tr_9"
        ),
    ):
        assert not verify_signature(ident.public_key_b64, tampered, h["X-Signature"])


def test_signature_from_another_key_is_rejected(tmp_path, ident):
    other = new_identity(tmp_path, "b")
    h = sign_request(ident, "GET", "/x")
    msg = canonical_string("GET", "/x", ident.device_id, h["X-Timestamp"], h["X-Nonce"], "")
    assert not verify_signature(other.public_key_b64, msg, h["X-Signature"])


@pytest.mark.parametrize("sig", ["", "!!!", "AAAA", "x" * 200])
def test_garbage_signatures_and_keys_are_rejected_without_raising(ident, sig):
    assert not verify_signature(ident.public_key_b64, b"m", sig)
    assert not verify_signature("not-a-key", b"m", sig)


def test_each_signed_request_gets_a_fresh_nonce(ident):
    assert (
        sign_request(ident, "GET", "/x")["X-Nonce"] != sign_request(ident, "GET", "/x")["X-Nonce"]
    )


def test_timestamps_round_trip_and_reject_garbage():
    now = datetime(2026, 10, 1, 10, 20, 5, tzinfo=UTC)
    assert format_timestamp(now) == "2026-10-01T10:20:05Z"
    assert parse_timestamp("2026-10-01T10:20:05Z") == now
    for bad in ("", "yesterday", "2026-10-01", "2026-10-01T10:20:05+00:00", "2026-13-01T00:00:00Z"):
        assert parse_timestamp(bad) is None


def test_timestamp_window_is_symmetric():
    now = datetime(2026, 10, 1, 10, 0, 0, tzinfo=UTC)
    edge = timedelta(seconds=SIGNATURE_MAX_SKEW_SECONDS)
    assert timestamp_is_fresh(now - edge, now) and timestamp_is_fresh(now + edge, now)
    assert not timestamp_is_fresh(now - edge - timedelta(seconds=1), now)
    assert not timestamp_is_fresh(now + edge + timedelta(seconds=1), now)


def test_nonce_cache_detects_replays_and_expires():
    t = [0.0]
    cache = NonceCache(ttl=10, clock=lambda: t[0])
    assert cache.check_and_store("d", "n1")
    assert not cache.check_and_store("d", "n1")  # replay
    assert cache.check_and_store("d", "n2")
    assert cache.check_and_store("other", "n1")  # scoped per device
    t[0] = 11.0
    assert cache.check_and_store("d", "n1")  # expired, forgotten


def test_failure_limiter_blocks_after_the_limit_and_recovers():
    t = [0.0]
    lim = FailureLimiter(limit=3, window=10, clock=lambda: t[0])
    assert not lim.blocked("ip")
    assert [lim.record("ip") for _ in range(3)] == [False, False, True]  # True once, at the limit
    assert lim.blocked("ip")
    assert not lim.blocked("other")
    assert lim.record("ip") is False  # not signalled again
    t[0] = 11.0
    assert not lim.blocked("ip")

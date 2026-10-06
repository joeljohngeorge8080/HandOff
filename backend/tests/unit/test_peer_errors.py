import pytest

from handoff.errors import HandOffError
from handoff.peer_api.errors import HTTP_STATUS, status_for

# Every code in API.md section 33 plus the ones M2 introduced must map to a deliberate status.
API_CODES = [
    "DEVICE_NOT_FOUND",
    "DEVICE_OFFLINE",
    "DEVICE_ALREADY_CONNECTED",
    "PEER_CONNECTION_FAILED",
    "INVALID_DEVICE_ID",
    "RECEIVER_NOT_READY",
    "FILE_NOT_FOUND",
    "FILE_TYPE_NOT_SUPPORTED",
    "FILE_TOO_LARGE",
    "INVALID_FILE",
    "FILE_STORAGE_ERROR",
    "TRANSFER_NOT_FOUND",
    "TRANSFER_ALREADY_EXISTS",
    "TRANSFER_FAILED",
    "TRANSFER_INCOMPLETE",
    "TRANSFER_VALIDATION_FAILED",
    "TRANSFER_ARCHIVE_INVALID",
    "TRANSFER_ARCHIVE_EXTRACTION_FAILED",
    "NETWORK_ERROR",
    "REQUEST_TIMEOUT",
    "CONNECTION_RESET",
    "INVALID_API_VERSION",
    "INVALID_REQUEST",
    "INVALID_STATE",
    "INTERNAL_ERROR",
    "INVALID_SIGNATURE",
    "REPLAYED_REQUEST",
    "DEVICE_NOT_TRUSTED",
    "INSUFFICIENT_STORAGE",
    "RATE_LIMITED",
    "INVALID_PATH",
    "INVALID_HASH",
]


def test_documented_status_mappings():
    expect = {
        "FILE_TOO_LARGE": 413,
        "INVALID_SIGNATURE": 401,
        "DEVICE_NOT_TRUSTED": 403,
        "TRANSFER_NOT_FOUND": 404,
        "INSUFFICIENT_STORAGE": 507,
        "RATE_LIMITED": 429,
        "INTERNAL_ERROR": 500,
    }
    for code, status in expect.items():
        assert status_for(HandOffError(code, "m")) == status


@pytest.mark.parametrize("code", [c for c in API_CODES if c not in HTTP_STATUS])
def test_codes_without_a_status_are_only_client_side_codes(code):
    # These are produced and consumed by the sender side, never returned by the peer API.
    assert code in {"DEVICE_OFFLINE", "PEER_CONNECTION_FAILED", "NETWORK_ERROR"}


def test_explicit_status_overrides_the_default():
    assert status_for(HandOffError("INVALID_REQUEST", "m", http_status=413)) == 413


def test_unknown_codes_default_to_400():
    assert status_for(HandOffError("SOMETHING_NEW", "m")) == 400

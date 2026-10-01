import httpx
import pytest

from handoff.devices.client import _is_cert_error, error_from_response, translate_transport_error
from handoff.errors import HandOffError, must


@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (httpx.ConnectError("refused"), "PEER_CONNECTION_FAILED"),
        (httpx.ConnectTimeout("slow"), "PEER_CONNECTION_FAILED"),
        (httpx.ReadTimeout("slow"), "REQUEST_TIMEOUT"),
        (httpx.WriteTimeout("slow"), "REQUEST_TIMEOUT"),
        (httpx.RemoteProtocolError("bye"), "CONNECTION_RESET"),
        (httpx.ReadError("reset"), "CONNECTION_RESET"),
        (httpx.WriteError("reset"), "CONNECTION_RESET"),
        (httpx.DecodingError("bad"), "NETWORK_ERROR"),
    ],
)
def test_transport_errors_become_stable_codes(exc, code):
    assert translate_transport_error(exc).code == code


def test_peer_error_responses_are_rebuilt_faithfully():
    resp = httpx.Response(
        409,
        json={"error": {"code": "RECEIVE_MODE_DISABLED", "message": "no", "details": {"a": 1}}},
    )
    e = error_from_response(resp)
    assert (e.code, e.message, e.details) == ("RECEIVE_MODE_DISABLED", "no", {"a": 1})


@pytest.mark.parametrize(
    "resp",
    [
        httpx.Response(500, content=b"<html>oops</html>"),
        httpx.Response(500, json={"unexpected": True}),
        httpx.Response(500, json={"error": "not an object"}),
        httpx.Response(500, json=[1, 2]),
    ],
)
def test_unexpected_error_bodies_do_not_crash_the_client(resp):
    e = error_from_response(resp)
    assert e.code == "NETWORK_ERROR" and "500" in e.message


def test_hostile_error_bodies_are_bounded():
    resp = httpx.Response(
        400, json={"error": {"code": "X" * 500, "message": "m" * 5000, "details": "not-a-dict"}}
    )
    e = error_from_response(resp)
    assert len(e.code) <= 64 and len(e.message) <= 300 and e.details == {}


def test_certificate_errors_are_recognised_through_exception_chains():
    import ssl

    inner = ssl.SSLCertVerificationError("bad cert")
    outer = httpx.ConnectError("wrapped")
    outer.__cause__ = inner
    assert _is_cert_error(outer) and not _is_cert_error(httpx.ConnectError("plain"))


def test_must_unwraps_values_and_turns_a_miss_into_an_internal_error():
    assert must(5) == 5 and must(0) == 0
    with pytest.raises(HandOffError) as e:
        must(None, "widget")
    assert e.value.code == "INTERNAL_ERROR" and "widget" in e.value.message

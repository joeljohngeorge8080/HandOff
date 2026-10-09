"""POST /api/v1/handoff/claim (ADR-058): the laptop the closed hand was carried to asks the
grabbing laptop to send what it holds."""

from handoff.errors import HandOffError


def err(resp):
    return resp.json()["error"]["code"]


def trust_alice(h):
    assert h.connect().status_code == 200  # connecting makes the caller a trusted, active peer


def test_a_trusted_peer_claim_reaches_the_bridge_with_the_callers_identity(h):
    seen = []
    h.ctx.claims = lambda who: seen.append(who) or {"transfer_id": "t1"}
    trust_alice(h)
    r = h.call("POST", "/api/v1/handoff/claim", json={})
    assert r.status_code == 202 and r.json() == {"transfer_id": "t1"}
    assert seen == [h.alice.device_id]  # from the verified signature, never from the body


def test_the_claimer_cannot_choose_who_is_asked_by_putting_an_id_in_the_body(h):
    seen = []
    h.ctx.claims = lambda who: seen.append(who) or {"transfer_id": "t1"}
    trust_alice(h)
    h.call("POST", "/api/v1/handoff/claim", json={"device_id": h.bob.device_id})
    assert seen == [h.alice.device_id]


def test_nothing_held_is_a_409_with_the_standard_error(h):
    def refuse(_who):
        raise HandOffError("NOTHING_HELD", "The other device is not holding anything.")

    h.ctx.claims = refuse
    trust_alice(h)
    r = h.call("POST", "/api/v1/handoff/claim", json={})
    assert r.status_code == 409 and err(r) == "NOTHING_HELD"


def test_an_unsigned_claim_is_refused(h):
    h.ctx.claims = lambda who: {"transfer_id": "t1"}
    trust_alice(h)
    r = h.call("POST", "/api/v1/handoff/claim", sign=False, json={})
    assert r.status_code == 401


def test_a_claim_from_an_untrusted_device_is_refused_before_anything_is_sent(h):
    called = []
    h.ctx.claims = lambda who: called.append(who) or {"transfer_id": "t1"}
    r = h.call("POST", "/api/v1/handoff/claim", who=h.bob, json={})  # never connected
    assert r.status_code in (401, 403) and called == []


def test_without_a_handler_the_endpoint_just_says_nothing_is_held(h):
    trust_alice(h)
    r = h.call("POST", "/api/v1/handoff/claim", json={})
    assert r.status_code == 409 and err(r) == "NOTHING_HELD"


def test_get_on_the_claim_path_is_not_allowed(h):
    trust_alice(h)
    assert h.call("GET", "/api/v1/handoff/claim").status_code == 405

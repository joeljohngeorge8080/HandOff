import json

import pytest
from helpers import make_zip
from test_transfers_api import FILES

from handoff.audit import AuditEvent
from handoff.errors import HandOffError
from handoff.ipc.dispatcher import Dispatcher


@pytest.fixture
def opened(h):
    h.connect()
    seen = []
    h.core.opener = seen.append
    return seen


def send(h, tid, files):
    assert h.start_transfer(tid, files).status_code in (200, 201)
    return h.upload(tid, make_zip(files))


def test_auto_open_is_off_by_default(h, opened):
    assert h.core.settings.auto_open_received() is False
    r = send(h, "t1", FILES)
    assert r.status_code == 200
    assert opened == []


def test_received_files_are_opened_when_enabled(h, opened):
    h.core.settings.set("auto_open_received", True)
    r = send(h, "t1", {"photo.jpg": b"J" * 100})
    assert r.status_code == 200
    assert opened == [h.inbox / "photo.jpg"]


def test_the_name_actually_written_is_opened(h, opened):
    (h.inbox / "photo.jpg").write_bytes(b"old")
    h.core.settings.set("auto_open_received", True)
    send(h, "t1", {"photo.jpg": b"J" * 100})
    assert opened == [h.inbox / "photo(1).jpg"]


def test_a_failing_opener_never_fails_the_transfer(h, opened):
    def boom(_path):
        raise HandOffError("OPEN_FAILED", "no viewer")

    h.core.opener = boom
    h.core.settings.set("auto_open_received", True)
    r = send(h, "t1", {"photo.jpg": b"J" * 100})
    assert r.status_code == 200
    assert r.json()["status"] == "completed"
    assert (h.inbox / "photo.jpg").exists()
    assert h.audit(AuditEvent.AUTO_OPEN_FAILED)


def test_an_unexpected_opener_error_does_not_undo_a_completed_transfer(h, opened):
    def boom(_path):
        raise RuntimeError("viewer exploded")

    h.core.opener = boom
    h.core.settings.set("auto_open_received", True)
    r = send(h, "t1", {"photo.jpg": b"J" * 100})
    assert r.status_code == 200
    assert r.json()["status"] == "completed"
    assert [t.status for t in h.transfer_rows()] == ["completed"]
    assert h.audit(AuditEvent.AUTO_OPEN_FAILED)


def test_a_failed_transfer_opens_nothing(h, opened):
    h.core.settings.set("auto_open_received", True)
    files = {"photo.jpg": b"J" * 100}
    assert h.start_transfer("t1", files).status_code in (200, 201)
    r = h.upload("t1", make_zip({"photo.jpg": b"tampered"}))
    assert r.status_code >= 400
    assert opened == []


def test_a_file_that_failed_verification_is_not_opened(h, opened):
    h.core.settings.set("auto_open_received", True)
    files = {"a.txt": b"hello", "b.txt": b"world"}
    assert h.start_transfer("t1", files).status_code in (200, 201)
    h.upload("t1", make_zip({"a.txt": b"hello", "b.txt": b"WORLD"}))
    assert all(p.name != "b.txt" for p in opened)


def test_at_most_a_few_files_are_opened_per_transfer(h, opened):
    from handoff.config import MAX_AUTO_OPEN_FILES

    h.core.settings.set("auto_open_received", True)
    files = {f"n{i}.txt": b"x" * (i + 1) for i in range(MAX_AUTO_OPEN_FILES + 3)}
    send(h, "t1", files)
    assert len(opened) == MAX_AUTO_OPEN_FILES
    assert len(list(h.inbox.iterdir())) == len(files)


def test_setting_must_be_a_boolean_and_is_audited(h):
    with pytest.raises(HandOffError):
        h.core.settings.set("auto_open_received", "yes")
    h.core.settings.set("auto_open_received", True)
    h.core.settings.set("auto_open_received", False)
    assert h.audit(AuditEvent.AUTO_OPEN_ENABLED)
    assert h.audit(AuditEvent.AUTO_OPEN_DISABLED)


def test_ui_can_change_it_locally_over_ipc(h):
    d = Dispatcher(h.core)
    msg = {
        "id": 1,
        "action": "settings.set",
        "payload": {"key": "auto_open_received", "value": True},
    }
    out = json.loads(d.handle_line(json.dumps(msg)))
    assert out["result"]["settings"]["auto_open_received"] is True


def test_a_peer_cannot_change_it(h):
    h.connect()
    r = h.call("PUT", "/api/v1/settings", json={"auto_open_received": True})
    assert r.status_code in (404, 405)
    assert h.core.settings.auto_open_received() is False

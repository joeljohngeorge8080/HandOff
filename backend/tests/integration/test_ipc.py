import json
import os
import subprocess
import sys

import pytest
from helpers import add_peer, add_transfer
from sqlalchemy import select

from handoff.audit import AuditEvent
from handoff.config import MAX_IPC_LINE_BYTES
from handoff.core import Core
from handoff.db.models import AuditLog, Transfer
from handoff.db.repositories import AuditRepository
from handoff.ipc.dispatcher import Dispatcher


@pytest.fixture
def d(core):
    return Dispatcher(core)


def call(d, action, payload=None, req_id=1):
    msg = {"id": req_id, "action": action}
    if payload is not None:
        msg["payload"] = payload
    return json.loads(d.handle_line(json.dumps(msg)))


def test_files_add_list_get_delete_flow(d, make_file):
    good = make_file("photo.jpg", b"jpeg")
    bad = make_file("doc.pdf", b"pdf")
    r = call(d, "files.add", {"paths": [str(good), str(bad), str(good.parent / "nope.txt")]})
    assert r["id"] == 1
    assert [f["name"] for f in r["result"]["added"]] == ["photo.jpg"]
    codes = [x["error"]["code"] for x in r["result"]["rejected"]]
    assert codes == ["FILE_TYPE_NOT_SUPPORTED", "FILE_NOT_FOUND"]

    fid = r["result"]["added"][0]["id"]
    assert call(d, "files.list")["result"]["files"][0]["id"] == fid
    assert call(d, "files.get", {"file_id": fid})["result"]["name"] == "photo.jpg"
    assert call(d, "files.delete", {"file_id": fid})["result"] == {"deleted": fid}
    assert call(d, "files.list")["result"]["files"] == []
    err = call(d, "files.get", {"file_id": fid})
    assert err["error"]["code"] == "FILE_NOT_FOUND" and "result" not in err


@pytest.mark.parametrize(
    "payload",
    [None, {}, {"paths": []}, {"paths": "a.txt"}, {"paths": [1]}, {"paths": [None]}],
)
def test_files_add_validates_its_payload(d, payload):
    assert call(d, "files.add", payload)["error"]["code"] == "INVALID_REQUEST"


@pytest.mark.parametrize("fid", [None, "", 5, ["a"]])
def test_file_id_must_be_a_non_empty_string(d, fid):
    assert call(d, "files.delete", {"file_id": fid})["error"]["code"] == "INVALID_REQUEST"


@pytest.mark.parametrize(
    "line",
    [
        "not json",
        "[]",
        '"str"',
        "{}",
        '{"id": 1}',
        '{"id": 1, "action": ""}',
        '{"id": 1, "action": 5}',
        '{"id": true, "action": "files.list"}',
        '{"id": null, "action": "files.list"}',
        '{"id": 1, "action": "files.list", "payload": []}',
        '{"id": 1, "action": "nope.nothing"}',
    ],
)
def test_malformed_requests_get_a_structured_error_never_a_crash(d, line):
    out = json.loads(d.handle_line(line))
    assert out["error"]["code"] == "INVALID_REQUEST"
    assert "message" in out["error"]


def test_error_responses_echo_the_request_id(d):
    out = call(d, "nope.nothing", req_id="abc-1")
    assert out["id"] == "abc-1" and out["error"]["code"] == "INVALID_REQUEST"


def test_oversized_request_is_rejected(d):
    out = json.loads(d.handle_line("x" * (MAX_IPC_LINE_BYTES + 1)))
    assert out["error"]["code"] == "INVALID_REQUEST"


def test_unexpected_errors_are_masked_and_do_not_leak_internals(d, core, monkeypatch):
    def boom():
        raise RuntimeError("secret internal path /home/x/.key")

    monkeypatch.setattr(core.files, "list_files", boom)
    out = call(d, "files.list")
    assert out["error"]["code"] == "INTERNAL_ERROR"
    assert "secret" not in json.dumps(out)


# ---------------------------------------------------------------- receive mode / settings


def test_receive_mode_defaults_off_and_can_be_enabled_without_a_peer(d):
    assert call(d, "receive_mode.get")["result"] == {"enabled": False}
    assert call(d, "receive_mode.set", {"enabled": True})["result"] == {"enabled": True}
    assert call(d, "receive_mode.get")["result"] == {"enabled": True}


def test_receive_mode_persists_across_restart(paths):
    c = Core(paths, hostname="T")
    c.start()
    Dispatcher(c).handle_line(
        json.dumps({"id": 1, "action": "receive_mode.set", "payload": {"enabled": True}})
    )
    c.close()
    c2 = Core(paths, hostname="T")
    c2.start()
    assert c2.settings.get_receive_mode() is True
    c2.close()


@pytest.mark.parametrize("value", ["true", 1, 0, None, "on", []])
def test_receive_mode_requires_a_real_boolean(d, value):
    assert call(d, "receive_mode.set", {"enabled": value})["error"]["code"] == "INVALID_REQUEST"
    assert call(d, "receive_mode.get")["result"] == {"enabled": False}


def test_receive_mode_changes_are_audited_with_old_and_new_values(d, core):
    call(d, "receive_mode.set", {"enabled": True})
    call(d, "receive_mode.set", {"enabled": True})  # no change, no event
    call(d, "receive_mode.set", {"enabled": False})
    with core.db.session() as s:
        rows = AuditRepository(s).list(event_type=AuditEvent.RECEIVE_MODE_CHANGED.value)
    assert [json.loads(r.meta) for r in reversed(rows)] == [
        {"old_value": False, "new_value": True},
        {"old_value": True, "new_value": False},
    ]


def test_settings_defaults_and_device_name_is_derived_from_the_os(d):
    s = call(d, "settings.get")["result"]["settings"]
    assert s["receive_mode"] is False
    assert s["history_retention"] == 90
    assert s["device_name"] == "Test-Laptop"
    assert "schema_version" not in s


def test_history_retention_is_configurable_and_validated(d):
    ok = call(d, "settings.set", {"key": "history_retention", "value": 30})
    assert ok["result"]["settings"]["history_retention"] == 30
    assert call(d, "settings.set", {"key": "history_retention", "value": 0})["result"]
    for bad in (-1, "30", True, None, 1.5):
        r = call(d, "settings.set", {"key": "history_retention", "value": bad})
        assert r["error"]["code"] == "INVALID_REQUEST"


@pytest.mark.parametrize("key", ["device_name", "schema_version", "receive_mode", "whatever"])
def test_non_editable_settings_cannot_be_changed(d, key):
    assert call(d, "settings.set", {"key": key, "value": "x"})["error"]["code"] == "INVALID_REQUEST"


def test_settings_set_requires_a_value(d):
    assert (
        call(d, "settings.set", {"key": "history_retention"})["error"]["code"] == "INVALID_REQUEST"
    )


# ---------------------------------------------------------------- history / status


def test_history_list_and_paging(d, core):
    add_peer(core)
    from datetime import timedelta

    from handoff.db.models import utcnow

    for i in range(5):
        add_transfer(core, f"tr_{i}", created_at=utcnow() - timedelta(minutes=10 - i))
    items = call(d, "history.list", {"limit": 2})["result"]["items"]
    assert [t["transfer_id"] for t in items] == ["tr_4", "tr_3"]  # newest first
    page2 = call(d, "history.list", {"limit": 2, "offset": 2})["result"]["items"]
    assert [t["transfer_id"] for t in page2] == ["tr_2", "tr_1"]
    first = items[0]
    assert first["direction"] == "sent" and first["peer_device_name"] == "Aaron-Laptop"
    assert first["files"][0] == {
        "name": "photo.jpg", "size": 10, "status": "completed", "failure_code": None,
    }  # fmt: skip


@pytest.mark.parametrize(
    "payload", [{"limit": 0}, {"limit": 201}, {"limit": "5"}, {"offset": -1}, {"limit": True}]
)
def test_history_paging_is_bounded(d, payload):
    assert call(d, "history.list", payload)["error"]["code"] == "INVALID_REQUEST"


def test_partially_completed_jobs_show_which_files_failed(d, core):
    add_peer(core)
    add_transfer(
        core, "tr_p", status="partially_completed",
        files=[("a.jpg", "completed", None), ("b.exe", "failed", None)],
    )  # fmt: skip
    (item,) = call(d, "history.list")["result"]["items"]
    assert item["status"] == "partially_completed"
    assert [(f["name"], f["status"]) for f in item["files"]] == [
        ("a.jpg", "completed"),
        ("b.exe", "failed"),
    ]


def test_status_snapshot_shape(d, core):
    snap = call(d, "status.snapshot")["result"]
    assert snap["device"] == {"device_id": core.identity.device_id, "device_name": "Test-Laptop"}
    assert snap["receive_mode"] is False
    assert snap["connection"] == {"connected": False, "device": None}
    assert snap["active_transfer"] is None and snap["recent_history"] == []


def test_status_snapshot_reports_the_active_transfer(d, core):
    add_peer(core)
    add_transfer(core, "tr_live", status="transferring", files=[("a.txt", "transferring", None)])
    assert call(d, "status.snapshot")["result"]["active_transfer"]["transfer_id"] == "tr_live"


# ---------------------------------------------------------------- crash recovery / startup


def test_transfers_interrupted_by_a_crash_are_failed_on_startup(paths):
    c = Core(paths, hostname="T")
    c.start()
    add_peer(c)
    add_transfer(
        c, "tr_crash", status="transferring",
        files=[
            ("a.txt", "completed", None),
            ("b.txt", "transferring", None),
            ("c.txt", "pending", None),
        ],
    )  # fmt: skip
    (paths.transfers_dir / "tr_crash").mkdir()
    (paths.transfers_dir / "tr_crash" / "payload.zip").write_bytes(b"partial")
    (paths.temp_dir / "import-x.part").write_bytes(b"partial")
    c.close()

    c2 = Core(paths, hostname="T")
    c2.start()
    with c2.db.session() as s:
        t = s.get(Transfer, "tr_crash")
        assert t.status == "failed" and t.error_code == "TRANSFER_INCOMPLETE"
        assert t.completed_at is not None
        assert [f.status for f in t.files] == ["completed", "failed", "failed"]
        events = AuditRepository(s).list(event_type="TRANSFER_FAILED", transfer_id="tr_crash")
        assert len(events) == 1
    assert list(paths.transfers_dir.iterdir()) == []
    assert list(paths.temp_dir.iterdir()) == []
    assert c2.history.active() is None
    c2.close()


def test_startup_and_shutdown_are_audited(paths):
    c = Core(paths, hostname="T")
    c.start()
    c.close()
    c = Core(paths, hostname="T")
    c.start()
    with c.db.session() as s:
        kinds = [r.event_type for r in s.scalars(select(AuditLog).order_by(AuditLog.id))]
    assert kinds == ["APPLICATION_STARTED", "APPLICATION_STOPPED", "APPLICATION_STARTED"]
    c.close()


def test_the_real_sidecar_process_speaks_the_ipc_protocol(tmp_path):
    src = tmp_path / "hello.txt"
    src.write_bytes(b"hi")
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    proc = subprocess.Popen(
        [sys.executable, "-m", "handoff", "--data-dir", str(tmp_path / "data"), "--no-network"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", env=env,
    )  # fmt: skip
    try:
        ready = json.loads(proc.stdout.readline())
        assert ready["event"] == "ready" and len(ready["device_id"]) == 36

        def rpc(msg: str):
            proc.stdin.write(msg + "\n")
            proc.stdin.flush()
            return json.loads(proc.stdout.readline())

        added = rpc(json.dumps({"id": 1, "action": "files.add", "payload": {"paths": [str(src)]}}))
        assert added["result"]["added"][0]["name"] == "hello.txt"
        assert rpc(json.dumps({"id": 2, "action": "files.list"}))["result"]["files"][0]["size"] == 2
        assert rpc("garbage")["error"]["code"] == "INVALID_REQUEST"
        big = rpc("x" * (MAX_IPC_LINE_BYTES + 5000))
        assert big["error"]["code"] == "INVALID_REQUEST"
        # the stream stays in sync after an oversized line
        assert rpc(json.dumps({"id": 3, "action": "receive_mode.get"}))["id"] == 3
    finally:
        proc.stdin.close()
        assert proc.wait(timeout=15) == 0
        stderr = proc.stderr.read()
        proc.stdout.close()
        proc.stderr.close()
    assert "Traceback" not in stderr


def test_sidecar_reports_a_damaged_database_and_exits_nonzero(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "handoff.db").write_bytes(b"garbage" * 100)
    out = subprocess.run(
        [sys.executable, "-m", "handoff", "--data-dir", str(data), "--no-network"],
        input="", capture_output=True, text=True, timeout=30,
    )  # fmt: skip
    assert out.returncode == 1
    assert json.loads(out.stdout.splitlines()[0])["error"]["code"] == "INTERNAL_ERROR"
    assert [p.name for p in data.glob("*.db")] == ["handoff.db"]

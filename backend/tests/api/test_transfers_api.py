import pytest
from helpers import make_zip, transfer_body
from sqlalchemy import select

from handoff.config import MAX_FILE_SIZE, MAX_FILES_PER_TRANSFER
from handoff.db.models import File, Transfer
from handoff.db.repositories import DeviceRepository

FILES = {"photo.jpg": b"J" * 100, "scan.png": b"V" * 5000, "notes.txt": b"hello"}


def err(resp):
    return resp.json()["error"]["code"]


@pytest.fixture
def ready(h):
    """alice is connected and the receive folder is a fresh temp directory."""
    assert h.connect().status_code == 200
    return h


def db_files(h):
    with h.core.db.session() as s:
        return list(s.scalars(select(File)))


def only_transfer(h) -> Transfer:
    with h.core.db.session() as s:
        from handoff.db.repositories import TransferRepository

        (t,) = TransferRepository(s).list_history()
        return t


def received_files(h):
    """Names in the receiver's chosen folder (ADR-055), not in HandOff storage."""
    return sorted(p.name for p in h.inbox.iterdir())


# ------------------------------------------------------------------------- creation


def test_transfer_is_rejected_when_the_receive_folder_is_unavailable(h, tmp_path):
    h.connect()
    gone = tmp_path / "vanished"
    gone.mkdir()
    h.core.settings.set_receive_directory(str(gone))
    gone.rmdir()
    r = h.start_transfer("tr_1", FILES)
    assert r.status_code == 409 and err(r) == "RECEIVER_NOT_READY"
    assert h.transfer_rows() == []
    assert any(a.meta and "RECEIVER_NOT_READY" in a.meta for a in h.audit("TRANSFER_REJECTED"))


def test_the_default_receive_folder_is_the_desktop(h, isolated_desktop):
    h.connect()
    # a fresh install never chose a folder: the OS Desktop is used
    with h.core.db.session() as s:
        from handoff.db.models import Setting

        s.delete(s.get(Setting, "receive_directory"))
    assert h.core.settings.receive_directory() == isolated_desktop.resolve()
    assert h.start_transfer("tr_1", FILES).status_code == 201
    assert h.upload("tr_1", make_zip(FILES)).status_code == 200
    assert sorted(p.name for p in isolated_desktop.iterdir()) == sorted(FILES)


def test_receiving_needs_no_receive_mode_just_trust(h):
    """ADR-055: there is no Receive Mode; a trusted connected peer can always send."""
    h.connect()
    assert h.start_transfer("tr_1", FILES).status_code == 201


def test_unconnected_untrusted_device_cannot_start_a_transfer(h):
    r = h.start_transfer("tr_1", FILES)
    assert r.status_code == 403 and err(r) == "DEVICE_NOT_TRUSTED"
    assert h.transfer_rows() == [] and h.audit("INVALID_DEVICE")
    assert received_files(h) == []


def test_accepted_transfer_is_recorded_and_ready_for_data(ready):
    r = ready.start_transfer("tr_1", FILES)
    assert r.status_code == 201
    assert r.json() == {
        "transfer_id": "tr_1", "status": "accepted", "upload_url": "/api/v1/transfers/tr_1/data",
    }  # fmt: skip
    t = only_transfer(ready)
    assert (t.direction, t.status, t.file_count) == ("received", "accepted", 3)
    assert t.source_device_id == ready.alice.device_id and t.destination_device_id is None
    assert [f.status for f in t.files] == ["pending"] * 3
    assert (ready.core.paths.transfers_dir / "tr_1").is_dir()
    assert ready.audit("TRANSFER_CREATED")
    st = ready.call("GET", "/api/v1/transfers/tr_1").json()
    assert st["status"] == "accepted" and st["progress"] == 0 and st["file_count"] == 3


def test_retrying_with_the_same_idempotency_key_returns_the_same_transfer(ready):
    first = ready.start_transfer("tr_1", FILES, key="k-123")
    again = ready.start_transfer("tr_1", FILES, key="k-123")
    assert (first.status_code, again.status_code) == (201, 200)
    assert again.json() == first.json()
    assert len(ready.transfer_rows()) == 1


def test_reusing_a_transfer_id_without_the_key_is_rejected(ready):
    ready.start_transfer("tr_1", FILES, key="k-123")
    for key in (None, "other-key"):
        r = ready.start_transfer("tr_1", FILES, key=key)
        assert r.status_code == 409 and err(r) == "TRANSFER_ALREADY_EXISTS"


def test_only_one_transfer_can_be_active(ready):
    assert ready.start_transfer("tr_1", FILES).status_code == 201
    r = ready.start_transfer("tr_2", FILES)
    assert r.status_code == 409 and err(r) == "INVALID_STATE"
    assert len(ready.transfer_rows()) == 1
    assert ready.audit("TRANSFER_REJECTED")


def test_a_completed_transfer_id_can_never_be_replayed(ready):
    ready.start_transfer("tr_1", FILES)
    assert ready.upload("tr_1", make_zip(FILES)).status_code == 200
    r = ready.start_transfer("tr_1", FILES)
    assert r.status_code == 409 and err(r) == "TRANSFER_ALREADY_EXISTS"
    assert len(ready.transfer_rows()) == 1


@pytest.mark.parametrize(
    ("files", "patch", "status", "code", "event"),
    [
        ({"../../evil.txt": b"x"}, {}, 422, "INVALID_PATH", "INVALID_PATH"),
        ({"..\\evil.txt": b"x"}, {}, 422, "INVALID_PATH", "INVALID_PATH"),
        ({"/etc/passwd": b"x"}, {}, 422, "INVALID_PATH", "INVALID_PATH"),
        ({"CON.txt": b"x"}, {}, 422, "INVALID_FILE", "INVALID_FILENAME"),
        ({"setup.exe": b"x"}, {}, 422, "FILE_TYPE_NOT_SUPPORTED", "UNSUPPORTED_FILE"),
        ({"clip.mp4": b"x"}, {}, 422, "FILE_TYPE_NOT_SUPPORTED", "UNSUPPORTED_FILE"),
        ({"run.sh": b"x"}, {}, 422, "FILE_TYPE_NOT_SUPPORTED", "UNSUPPORTED_FILE"),
        ({"a.txt": b"x"}, {"file_count": 5}, 422, "TRANSFER_VALIDATION_FAILED", None),
        ({"a.txt": b"x"}, {"total_size": 99}, 422, "TRANSFER_VALIDATION_FAILED", None),
        ({"a.txt": b"x"}, {"archive_name": "../x.zip"}, 422, "TRANSFER_VALIDATION_FAILED", None),
        ({"a.txt": b"x"}, {"archive_name": 5}, 422, "TRANSFER_VALIDATION_FAILED", None),
        ({"a.txt": b"x"}, {"source_device_id": "someone-else"}, 400, "INVALID_DEVICE_ID", None),
        ({"a.txt": b"x"}, {"transfer_id": "../x"}, 422, "TRANSFER_VALIDATION_FAILED", None),
        ({"a.txt": b"x"}, {"files": []}, 422, "TRANSFER_VALIDATION_FAILED", None),
        ({"a.txt": b"x"}, {"files": "nope"}, 422, "TRANSFER_VALIDATION_FAILED", None),
    ],
)
def test_invalid_transfer_requests_are_rejected_before_anything_is_stored(
    ready, files, patch, status, code, event
):
    body = transfer_body("tr_1", files, source=ready.alice.device_id)
    body.update(patch)
    r = ready.call("POST", "/api/v1/transfers", json=body)
    assert r.status_code == status and err(r) == code
    assert ready.transfer_rows() == []
    assert not list(ready.core.paths.transfers_dir.iterdir())
    assert ready.audit("TRANSFER_REJECTED")
    if event:
        assert ready.audit(event)


def test_a_file_over_50_mb_is_rejected_by_the_receiver_whatever_the_sender_says(ready):
    body = transfer_body("tr_1", {"big.png": b"x"}, source=ready.alice.device_id)
    body["files"][0]["size"] = MAX_FILE_SIZE + 1
    body["total_size"] = MAX_FILE_SIZE + 1
    r = ready.call("POST", "/api/v1/transfers", json=body)
    assert r.status_code == 413 and err(r) == "FILE_TOO_LARGE"
    assert ready.audit("FILE_TOO_LARGE") and ready.transfer_rows() == []


def test_exactly_50_mb_is_accepted_at_creation(ready):
    body = transfer_body("tr_1", {"big.png": b"x"}, source=ready.alice.device_id)
    body["files"][0]["size"] = MAX_FILE_SIZE
    body["total_size"] = MAX_FILE_SIZE
    assert ready.call("POST", "/api/v1/transfers", json=body).status_code == 201


def test_too_many_files_are_rejected(ready):
    files = {f"n{i}.txt": b"x" for i in range(MAX_FILES_PER_TRANSFER + 1)}
    r = ready.start_transfer("tr_1", files)
    assert r.status_code == 422 and err(r) == "TRANSFER_VALIDATION_FAILED"
    ok = {f"n{i}.txt": b"x" for i in range(MAX_FILES_PER_TRANSFER)}
    assert ready.start_transfer("tr_2", ok).status_code == 201


def test_a_transfer_that_cannot_fit_on_disk_is_rejected_up_front(ready, monkeypatch):
    monkeypatch.setattr(ready.receiver, "_free_bytes", lambda where=None: 10)
    r = ready.start_transfer("tr_1", FILES)
    assert r.status_code == 507 and err(r) == "INSUFFICIENT_STORAGE"
    assert ready.transfer_rows() == [] and ready.audit("TRANSFER_REJECTED")


def test_a_destination_volume_without_space_is_rejected_up_front(ready, monkeypatch):
    """Staging has room, but the folder the user chose is on a nearly full disk."""
    real = ready.receiver._free_bytes

    def free(where=None):
        return 10 if where == ready.inbox.resolve() else real(where)

    monkeypatch.setattr(ready.receiver, "_free_bytes", free)
    r = ready.start_transfer("tr_1", FILES)
    assert r.status_code == 507 and err(r) == "INSUFFICIENT_STORAGE"
    assert ready.transfer_rows() == [] and received_files(ready) == []


def test_flooding_with_invalid_manifests_gets_the_sender_rate_limited(ready):
    for i in range(20):
        assert ready.start_transfer(f"tr_{i}", {"run.sh": b"x"}).status_code == 422
    r = ready.start_transfer("tr_ok", FILES)
    assert r.status_code == 429 and err(r) == "RATE_LIMITED"


def test_an_accepted_transfer_that_gets_no_data_expires(ready):
    assert ready.start_transfer("tr_1", FILES).status_code == 201
    ready.mono += 31  # past the (30 s) accept timeout
    assert ready.start_transfer("tr_2", FILES).status_code == 201
    with ready.core.db.session() as s:
        from handoff.db.repositories import TransferRepository

        old = TransferRepository(s).get("tr_1")
        assert old.status == "failed" and old.error_code == "REQUEST_TIMEOUT"
        assert [f.status for f in old.files] == ["failed"] * 3
    assert not (ready.core.paths.transfers_dir / "tr_1").exists()
    assert ready.call("POST", "/api/v1/transfers/tr_1/data", content=b"x").status_code in (404, 415)


# ------------------------------------------------------------------------- upload


def test_successful_multi_file_transfer(ready):
    ready.start_transfer("tr_1", FILES)
    r = ready.upload("tr_1", make_zip(FILES))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "completed" and body["files_received"] == 3
    assert [f["status"] for f in body["files"]] == ["completed"] * 3

    # files land in the receiver's own folder under their real names ...
    assert received_files(ready) == sorted(FILES)
    for name, data in FILES.items():
        assert (ready.inbox / name).read_bytes() == data
    # ... and are not HandOff-managed files (ADR-055): nothing in `files`, nothing in received/
    assert db_files(ready) == []
    assert list(ready.core.paths.received_dir.iterdir()) == []

    t = only_transfer(ready)
    assert t.status == "completed" and t.completed_at is not None
    assert [f.status for f in t.files] == ["completed"] * 3
    assert {f.file_id for f in t.files} == {None}
    assert sorted(f.original_name for f in t.files) == sorted(FILES)
    assert {f["saved_as"] for f in body["files"]} == set(FILES)
    assert len(ready.audit("FILE_RECEIVED")) == 3
    assert len(ready.audit("TRANSFER_COMPLETED")) == 1
    assert not (ready.core.paths.transfers_dir / "tr_1").exists()  # temp data removed

    st = ready.call("GET", "/api/v1/transfers/tr_1").json()
    assert st["status"] == "completed" and st["files_completed"] == 3 and st["progress"] == 1.0


def test_received_files_do_not_become_managed_files(ready):
    ready.start_transfer("tr_1", FILES)
    ready.upload("tr_1", make_zip(FILES))
    assert ready.core.files.list_files() == []


def test_name_collisions_never_overwrite_and_identical_content_is_still_copied(ready):
    for tid in ("tr_1", "tr_2"):
        ready.start_transfer(tid, {"photo.jpg": b"same"})
        assert ready.upload(tid, make_zip({"photo.jpg": b"same"})).status_code == 200
    assert received_files(ready) == ["photo(1).jpg", "photo.jpg"]
    ready.start_transfer("tr_3", {"photo.jpg": b"same"})
    body = ready.upload("tr_3", make_zip({"photo.jpg": b"same"})).json()
    # the contract key stays the sender's name; the name actually written is reported apart
    assert body["files"] == [
        {
            "name": "photo.jpg",
            "status": "completed",
            "failure_code": None,
            "saved_as": "photo(2).jpg",
        }
    ]
    assert received_files(ready) == ["photo(1).jpg", "photo(2).jpg", "photo.jpg"]


def test_an_existing_file_in_the_receive_folder_is_never_overwritten(ready):
    (ready.inbox / "photo.jpg").write_bytes(b"MINE - irreplaceable")
    ready.start_transfer("tr_1", {"photo.jpg": b"incoming"})
    assert ready.upload("tr_1", make_zip({"photo.jpg": b"incoming"})).status_code == 200
    assert (ready.inbox / "photo.jpg").read_bytes() == b"MINE - irreplaceable"
    assert (ready.inbox / "photo(1).jpg").read_bytes() == b"incoming"


def test_one_corrupted_file_gives_a_partial_result(ready):
    ready.start_transfer("tr_1", FILES)
    corrupted = {**FILES, "scan.png": b"X" * 5000}  # same size, different bytes
    r = ready.upload("tr_1", make_zip(corrupted))
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "partially_completed" and body["files_received"] == 2
    by = {f["name"]: f for f in body["files"]}
    assert by["scan.png"] == {
        "name": "scan.png",
        "status": "failed",
        "failure_code": "INVALID_HASH",
    }
    assert by["photo.jpg"]["status"] == by["notes.txt"]["status"] == "completed"

    t = only_transfer(ready)
    assert t.status == "partially_completed" and t.error_code == "INVALID_HASH"
    assert received_files(ready) == ["notes.txt", "photo.jpg"]  # the corrupted one is not written
    assert db_files(ready) == []
    assert len(ready.audit("INVALID_HASH")) == 1
    assert len(ready.audit("TRANSFER_PARTIALLY_COMPLETED")) == 1
    assert not ready.audit("TRANSFER_COMPLETED")


def test_every_file_corrupted_means_the_transfer_failed(ready):
    ready.start_transfer("tr_1", FILES)
    r = ready.upload("tr_1", make_zip({n: b"?" * len(d) for n, d in FILES.items()}))
    assert r.status_code == 422 and err(r) == "TRANSFER_FAILED"
    assert [f["status"] for f in r.json()["error"]["details"]["files"]] == ["failed"] * 3
    assert only_transfer(ready).status == "failed"
    assert db_files(ready) == [] and received_files(ready) == []


def test_a_malicious_archive_is_rejected_and_writes_nothing(ready, tmp_path):
    ready.start_transfer("tr_1", {"a.txt": b"x"})
    r = ready.upload("tr_1", make_zip({"../../outside.txt": b"pwned"}))
    assert r.status_code == 422 and err(r) == "INVALID_PATH"
    t = only_transfer(ready)
    assert t.status == "failed" and t.error_code == "INVALID_PATH"
    assert ready.audit("INVALID_PATH")
    assert received_files(ready) == [] and db_files(ready) == []
    assert not list(tmp_path.rglob("outside.txt"))
    assert not (ready.core.paths.transfers_dir / "tr_1").exists()


@pytest.mark.parametrize(
    ("zip_files", "code"),
    [
        ({"a.txt": b"x", "sneaky.txt": b"y"}, "TRANSFER_VALIDATION_FAILED"),
        ({"other.txt": b"x"}, "TRANSFER_VALIDATION_FAILED"),
    ],
)
def test_archives_that_do_not_match_the_manifest_are_rejected(ready, zip_files, code):
    ready.start_transfer("tr_1", {"a.txt": b"x"})
    r = ready.upload("tr_1", make_zip(zip_files))
    assert r.status_code == 422 and err(r) == code
    assert only_transfer(ready).status == "failed" and received_files(ready) == []


def test_a_payload_that_is_not_a_zip_is_rejected(ready):
    ready.start_transfer("tr_1", {"a.txt": b"x"})
    r = ready.upload("tr_1", b"definitely not a zip")
    assert r.status_code == 422 and err(r) == "TRANSFER_ARCHIVE_INVALID"
    assert only_transfer(ready).status == "failed"


def test_a_file_larger_than_declared_inside_the_archive_fails(ready):
    ready.start_transfer("tr_1", {"a.txt": b"x" * 10})
    body = make_zip({"a.txt": b"x" * 1000})
    r = ready.upload("tr_1", body)
    assert r.status_code == 422 and err(r) == "TRANSFER_FAILED"
    assert r.json()["error"]["details"]["files"][0]["failure_code"] == "TRANSFER_VALIDATION_FAILED"


def test_wrong_content_type_does_not_consume_the_transfer(ready):
    ready.start_transfer("tr_1", FILES)
    assert ready.upload("tr_1", make_zip(FILES), ctype="text/plain").status_code == 415
    assert only_transfer(ready).status == "accepted"
    assert ready.upload("tr_1", make_zip(FILES)).status_code == 200


def test_uploading_to_an_unknown_transfer_is_not_found(ready):
    r = ready.upload("tr_nope", make_zip(FILES))
    assert r.status_code == 404 and err(r) == "TRANSFER_NOT_FOUND"


def test_a_finished_transfer_cannot_receive_data_again(ready):
    ready.start_transfer("tr_1", FILES)
    ready.upload("tr_1", make_zip(FILES))
    r = ready.upload("tr_1", make_zip(FILES))
    assert r.status_code == 404
    assert received_files(ready) == sorted(FILES)  # nothing duplicated


def test_another_trusted_device_cannot_touch_someones_transfer(ready):
    with ready.core.db.session() as s:
        repo = DeviceRepository(s)
        repo.upsert(ready.bob.device_id, "Bob", "linux", public_key=ready.bob.public_key_b64)
        repo.set_trusted(ready.bob.device_id, True)
    ready.start_transfer("tr_1", FILES)
    assert ready.upload("tr_1", make_zip(FILES), who=ready.bob).status_code == 404
    assert ready.call("GET", "/api/v1/transfers/tr_1", who=ready.bob).status_code == 404
    assert only_transfer(ready).status == "accepted"


def test_an_upload_larger_than_the_declared_transfer_is_cut_off(ready):
    ready.start_transfer("tr_1", {"a.txt": b"hello"})
    r = ready.upload("tr_1", b"0" * 20_000)
    assert r.status_code == 413 and err(r) == "FILE_TOO_LARGE"
    t = only_transfer(ready)
    assert t.status == "failed" and t.error_code == "FILE_TOO_LARGE"
    assert not (ready.core.paths.transfers_dir / "tr_1").exists()


def test_a_receive_folder_that_disappears_before_the_data_arrives_fails_the_transfer(
    ready, tmp_path
):
    gone = tmp_path / "usb-stick"
    gone.mkdir()
    ready.core.settings.set_receive_directory(str(gone))
    ready.start_transfer("tr_1", FILES)
    gone.rmdir()
    r = ready.upload("tr_1", make_zip(FILES))
    assert r.status_code == 422 and err(r) == "TRANSFER_FAILED"
    t = only_transfer(ready)
    assert t.status == "failed" and t.error_code == "RECEIVER_NOT_READY"
    assert not (ready.core.paths.transfers_dir / "tr_1").exists()


def test_a_renamed_executable_is_refused_by_the_receiver(ready):
    """setup.exe renamed to photo.jpg: the extension passes but the content does not."""
    payload = {"photo.jpg": b"MZ\x90\x00 this is really a Windows program", "ok.txt": b"fine"}
    ready.start_transfer("tr_1", payload)
    r = ready.upload("tr_1", make_zip(payload))
    assert r.status_code == 200 and r.json()["status"] == "partially_completed"
    by = {f["name"]: f for f in r.json()["files"]}
    assert by["photo.jpg"]["failure_code"] == "FILE_TYPE_NOT_SUPPORTED"
    assert received_files(ready) == ["ok.txt"]
    assert ready.audit("UNSUPPORTED_FILE")


def test_a_failure_while_copying_one_file_does_not_remove_or_corrupt_others(ready, monkeypatch):
    import handoff.transfer.receiver as rx
    from handoff.errors import HandOffError

    real = rx.copy_verified

    def flaky(src, directory, name, expected):
        if name == "photo.jpg":
            raise HandOffError("FILE_STORAGE_ERROR", "disk said no")
        return real(src, directory, name, expected)

    monkeypatch.setattr(rx, "copy_verified", flaky)
    ready.start_transfer("tr_1", FILES)
    r = ready.upload("tr_1", make_zip(FILES))
    assert r.json()["status"] == "partially_completed"
    assert received_files(ready) == ["notes.txt", "scan.png"]


def test_an_internal_failure_after_files_were_moved_leaves_no_trace(ready, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("database exploded")

    monkeypatch.setattr(ready.receiver, "_record_results", boom)
    ready.start_transfer("tr_1", FILES)
    r = ready.upload("tr_1", make_zip(FILES))
    assert r.status_code == 500 and err(r) == "INTERNAL_ERROR"
    assert "exploded" not in r.text  # no internals leaked
    assert received_files(ready) == [] and db_files(ready) == []  # nothing left in the folder
    assert only_transfer(ready).status == "failed"
    assert not (ready.core.paths.transfers_dir / "tr_1").exists()


def test_status_of_an_unknown_or_malformed_transfer_id(ready):
    assert ready.call("GET", "/api/v1/transfers/tr_nope").status_code == 404
    # the server verifies the decoded path, so sign that form to reach the ID-shape check
    headers = ready.headers("GET", "/api/v1/transfers/bad id")
    r = ready.http.get("/api/v1/transfers/bad%20id", headers=headers)
    assert r.status_code == 404 and err(r) == "TRANSFER_NOT_FOUND"


# ---------------------------------------------- defence in depth (found by mutation testing)


def test_a_chunked_upload_without_content_length_is_still_capped(ready):
    """No Content-Length means the early size check can't run; the stream itself must be bounded."""
    ready.start_transfer("tr_1", {"a.txt": b"hello"})

    def endless():
        for _ in range(50):
            yield b"0" * 4096

    r = ready.call(
        "POST", "/api/v1/transfers/tr_1/data", content=endless(),
        headers={"Content-Type": "application/zip"},
    )  # fmt: skip
    assert r.status_code == 413 and err(r) == "FILE_TOO_LARGE"
    assert only_transfer(ready).status == "failed"
    assert not (ready.core.paths.transfers_dir / "tr_1").exists()


def test_a_replayed_transfer_id_is_caught_by_the_database_even_if_the_early_check_races(
    ready, monkeypatch
):
    ready.start_transfer("tr_1", FILES)
    ready.upload("tr_1", make_zip(FILES))
    monkeypatch.setattr(ready.receiver, "_check_not_replayed", lambda sender, tid: None)
    r = ready.start_transfer("tr_1", FILES)
    assert r.status_code == 409 and err(r) == "TRANSFER_ALREADY_EXISTS"
    assert len(ready.transfer_rows()) == 1


def test_an_unrelated_constraint_failure_is_not_reported_as_a_replay(ready, monkeypatch):
    from sqlalchemy.exc import IntegrityError

    def other_constraint(*a, **k):
        raise IntegrityError("INSERT", {}, Exception("FOREIGN KEY constraint failed"))

    from handoff.db.repositories import TransferRepository

    monkeypatch.setattr(TransferRepository, "add", other_constraint)
    r = ready.start_transfer("tr_1", FILES)
    assert r.status_code == 500 and err(r) == "INTERNAL_ERROR"


def test_a_trusted_device_that_is_not_the_connected_peer_cannot_send(ready):
    with ready.core.db.session() as s:
        repo = DeviceRepository(s)
        repo.upsert(ready.bob.device_id, "Bob", "linux", public_key=ready.bob.public_key_b64)
        repo.set_trusted(ready.bob.device_id, True)
    r = ready.start_transfer("tr_b", FILES, who=ready.bob)  # alice is the connected peer
    assert r.status_code == 409 and err(r) == "DEVICE_ALREADY_CONNECTED"
    assert ready.transfer_rows() == []
    assert ready.start_transfer("tr_a", FILES).status_code == 201  # the connected peer still can


def test_a_trusted_device_may_send_after_a_restart_when_nobody_is_connected(ready):
    ready.connections._active = None  # as after a restart: trust persisted, connection did not
    assert ready.start_transfer("tr_1", FILES).status_code == 201
    assert ready.connections.snapshot()["device"]["device_id"] == ready.alice.device_id

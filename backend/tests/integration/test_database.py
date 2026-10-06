from datetime import timedelta

import pytest
from helpers import add_peer, add_transfer
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError, StatementError

from handoff.audit import AuditEvent, record_event
from handoff.core import Core
from handoff.db.engine import open_database
from handoff.db.models import AuditLog, File, Setting, Transfer, TransferFile, utcnow
from handoff.db.repositories import AuditRepository, SettingsRepository, TransferRepository
from handoff.db.schema import REQUIRED_TABLES, SCHEMA_VERSION, init_schema
from handoff.errors import DatabaseInitError, HandOffError
from handoff.transfer.states import TransferStatus


def test_initialization_creates_the_six_phase1_tables(core):
    assert set(inspect(core.db.engine).get_table_names()) == REQUIRED_TABLES
    assert {
        "devices", "files", "transfers", "transfer_files", "settings", "audit_logs",
    } == REQUIRED_TABLES  # fmt: skip


def test_schema_version_is_recorded(core):
    with core.db.session() as s:
        assert SettingsRepository(s).get("schema_version") == SCHEMA_VERSION


def test_foreign_keys_are_enforced(core):
    with core.db.engine.connect() as c:
        assert c.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1


def test_transfer_to_nonexistent_device_is_rejected(core):
    with pytest.raises(IntegrityError), core.db.session() as s:
        s.add(
            Transfer(
                id="t", direction="sent", destination_device_id="ghost", status="completed",
                file_count=1, total_size_bytes=1, created_at=utcnow(),
            )
        )  # fmt: skip


def test_transfer_file_to_nonexistent_transfer_is_rejected(core):
    with pytest.raises(IntegrityError), core.db.session() as s:
        s.add(
            TransferFile(
                transfer_id="ghost", original_name="a.txt", size_bytes=1, sha256="0" * 64,
                status="pending", created_at=utcnow(),
            )
        )  # fmt: skip


def test_transfer_file_to_nonexistent_file_is_rejected(core):
    add_peer(core)
    with pytest.raises(IntegrityError):
        add_transfer(core, files=[("a.txt", "completed", "no-such-file")])


@pytest.mark.parametrize(
    "bad",
    [
        {"status": "cancelled"},
        {"direction": "sideways"},
        {"file_count": -1},
    ],
)
def test_invalid_transfer_values_are_rejected_by_the_database(core, bad):
    values = dict(
        id="t", direction="sent", status="completed", file_count=1, total_size_bytes=1,
        created_at=utcnow(),
    )  # fmt: skip
    values.update(bad)
    with pytest.raises(IntegrityError), core.db.session() as s:
        s.add(Transfer(**values))


def test_file_source_and_size_are_constrained(core):
    now = utcnow()
    base = dict(
        id="f", original_name="a.txt", stored_name="f", extension=".txt", size_bytes=1,
        sha256="0" * 64, source="imported", storage_path="files/f", created_at=now, updated_at=now,
    )  # fmt: skip
    for bad in ({"source": "cloud"}, {"size_bytes": -5}):
        with pytest.raises(IntegrityError), core.db.session() as s:
            s.add(File(**{**base, **bad}))


def test_naive_datetimes_are_refused(core):
    with pytest.raises(StatementError), core.db.session() as s:
        s.add(
            Setting(key="x", value="1", value_type="int", updated_at=utcnow().replace(tzinfo=None))
        )


def test_datetimes_round_trip_as_utc(core):
    with core.db.session() as s:
        row = s.get(Setting, "history_retention")
        assert row.updated_at.tzinfo is not None
        assert row.updated_at.utcoffset() == timedelta(0)


def test_only_one_transfer_can_be_active(core):
    add_peer(core)
    add_transfer(core, "tr_a", status="transferring")
    with pytest.raises(HandOffError) as e:
        add_transfer(core, "tr_b", status="created")
    assert e.value.code == "INVALID_STATE"


def test_a_new_transfer_is_allowed_once_the_previous_one_finished(core):
    add_peer(core)
    add_transfer(core, "tr_a", status="transferring")
    with core.db.session() as s:
        repo = TransferRepository(s)
        repo.set_status(repo.get("tr_a"), TransferStatus.COMPLETED)
    add_transfer(core, "tr_b", status="created")


def test_many_finished_transfers_do_not_conflict(core):
    add_peer(core)
    for i in range(3):
        add_transfer(core, f"tr_{i}", status="completed")


def test_repository_rejects_invalid_state_transition(core):
    add_peer(core)
    add_transfer(core, "tr_a", status="completed")
    with pytest.raises(HandOffError) as e, core.db.session() as s:
        repo = TransferRepository(s)
        repo.set_status(repo.get("tr_a"), TransferStatus.TRANSFERRING)
    assert e.value.code == "INVALID_STATE"


def test_set_status_records_timestamps_and_error(core):
    add_peer(core)
    add_transfer(core, "tr_a", status="accepted")
    with core.db.session() as s:
        repo = TransferRepository(s)
        t = repo.get("tr_a")
        repo.set_status(t, TransferStatus.TRANSFERRING)
        assert t.started_at is not None
        repo.set_status(t, TransferStatus.FAILED, error_code="NETWORK_ERROR", error_message="lost")
        assert t.completed_at is not None and t.error_code == "NETWORK_ERROR"


def test_settings_are_typed_and_persist(core):
    with core.db.session() as s:
        repo = SettingsRepository(s)
        repo.set("flag", True)
        repo.set("count", 7)
        repo.set("label", "hi")
    with core.db.session() as s:
        repo = SettingsRepository(s)
        assert repo.get("flag") is True
        assert repo.get("count") == 7
        assert repo.get("label") == "hi"
        assert repo.get("missing", "dflt") == "dflt"


def test_failed_transaction_rolls_back(core):
    with pytest.raises(RuntimeError), core.db.session() as s:
        SettingsRepository(s).set("temp", "x")
        raise RuntimeError("boom")
    with core.db.session() as s:
        assert SettingsRepository(s).get("temp") is None


# ---------------------------------------------------------------- schema upgrades


def test_older_database_is_upgraded_in_place_and_keeps_its_data(core):
    with core.db.session() as s:
        SettingsRepository(s).set("keepme", "yes")
    calls = []

    def v1_to_v2(conn):
        calls.append(1)
        conn.execute(text("CREATE TABLE upgrade_marker (x INTEGER)"))

    assert init_schema(core.db, target=2, migrations={1: v1_to_v2}) == 2
    assert calls == [1]
    with core.db.session() as s:
        assert SettingsRepository(s).get("schema_version") == 2
        assert SettingsRepository(s).get("keepme") == "yes"
    assert "upgrade_marker" in inspect(core.db.engine).get_table_names()
    # idempotent: running again does nothing
    assert init_schema(core.db, target=2, migrations={1: v1_to_v2}) == 2
    assert calls == [1]


def test_failed_upgrade_rolls_back_completely(core):
    def broken(conn):
        conn.execute(text("CREATE TABLE half_done (x INTEGER)"))
        raise RuntimeError("migration bug")

    with pytest.raises(RuntimeError):
        init_schema(core.db, target=2, migrations={1: broken})
    names = inspect(core.db.engine).get_table_names()
    assert "half_done" not in names
    with core.db.session() as s:
        assert SettingsRepository(s).get("schema_version") == 1
    assert set(names) >= REQUIRED_TABLES


def test_missing_upgrade_path_is_an_error_not_a_reset(core):
    with pytest.raises(DatabaseInitError):
        init_schema(core.db, target=3, migrations={})
    with core.db.session() as s:
        assert SettingsRepository(s).get("schema_version") == 1


def test_database_from_a_newer_version_is_refused(core):
    with core.db.session() as s:
        SettingsRepository(s).set("schema_version", SCHEMA_VERSION + 5)
    with pytest.raises(DatabaseInitError):
        init_schema(core.db)


def test_unversioned_existing_database_is_stamped_not_wiped(paths):
    c = Core(paths, hostname="X")
    c.start()
    with c.db.session() as s:
        SettingsRepository(s).set("keepme", "yes")
        s.delete(s.get(Setting, "schema_version"))
    c.close()
    c = Core(paths, hostname="X")
    c.start()
    with c.db.session() as s:
        assert SettingsRepository(s).get("keepme") == "yes"
        assert SettingsRepository(s).get("schema_version") == 1
    c.close()


def test_damaged_database_fails_clearly_and_never_creates_a_second_one(paths):
    paths.ensure()
    paths.db_path.write_bytes(b"this is definitely not a sqlite database" * 50)
    with pytest.raises(DatabaseInitError) as e:
        open_database(paths)
    assert "database" in e.value.message.lower()
    assert paths.db_path.read_bytes().startswith(b"this is definitely")  # untouched
    assert [p for p in paths.root.rglob("*.db")] == [paths.db_path]


def test_a_fresh_start_creates_the_database_automatically(paths):
    assert not paths.root.exists()
    c = Core(paths, hostname="X")
    c.start()
    assert paths.db_path.exists()
    c.close()


# ---------------------------------------------------------------- audit + retention


def test_audit_event_commits_with_the_change_it_describes(core):
    with pytest.raises(RuntimeError), core.db.session() as s:
        record_event(s, AuditEvent.TRANSFER_CREATED, "x", transfer_id="t")
        raise RuntimeError
    with core.db.session() as s:
        assert AuditRepository(s).list(transfer_id="t") == []
    with core.db.session() as s:
        record_event(
            s, AuditEvent.TRANSFER_CREATED, "x", transfer_id="t", device_id="d",
            metadata={"a": 1},
        )  # fmt: skip
    with core.db.session() as s:
        (row,) = AuditRepository(s).list(transfer_id="t")
        assert (row.event_type, row.device_id, row.meta) == ("TRANSFER_CREATED", "d", '{"a": 1}')


def test_history_retention_removes_only_old_finished_transfers(core):
    add_peer(core)
    now = utcnow()
    add_transfer(core, "old_done", status="completed", created_at=now - timedelta(days=200))
    add_transfer(core, "old_failed", status="failed", created_at=now - timedelta(days=100))
    add_transfer(core, "recent", status="completed", created_at=now - timedelta(days=5))
    add_transfer(core, "old_active", status="transferring", created_at=now - timedelta(days=300))
    core.settings.set("history_retention", 90)
    assert core.history.cleanup(now) == 2
    with core.db.session() as s:
        remaining = {t.id for t in s.scalars(select(Transfer))}
        assert remaining == {"recent", "old_active"}
        assert s.scalars(select(TransferFile)).first() is not None  # recent's file row remains


def test_retention_zero_means_forever(core):
    add_peer(core)
    add_transfer(core, "ancient", created_at=utcnow() - timedelta(days=5000))
    core.settings.set("history_retention", 0)
    assert core.history.cleanup() == 0
    assert len(core.history.list()) == 1


def test_retention_cleanup_never_touches_audit_logs(core):
    add_peer(core)
    now = utcnow()
    add_transfer(core, "old", created_at=now - timedelta(days=400))
    with core.db.session() as s:
        record_event(s, AuditEvent.TRANSFER_COMPLETED, "done", transfer_id="old")
        s.add(AuditLog(event_type="OLD", message="ancient", created_at=now - timedelta(days=900)))
    before = _audit_count(core)
    core.settings.set("history_retention", 30)
    assert core.history.cleanup(now) == 1
    assert _audit_count(core) == before


def _audit_count(core):
    with core.db.session() as s:
        return len(s.scalars(select(AuditLog)).all())


# ---------------------------------------------------------------- devices (ADR-048)


def test_new_devices_are_untrusted_until_the_user_trusts_them(core):
    from handoff.db.repositories import DeviceRepository

    with core.db.session() as s:
        d = DeviceRepository(s).upsert("peer-9", "Zed", "windows", public_key="PUBKEY")
        assert d.is_trusted is False and d.public_key == "PUBKEY"
    with core.db.session() as s:
        DeviceRepository(s).set_trusted("peer-9", True)
    with core.db.session() as s:
        assert DeviceRepository(s).get_by_device_id("peer-9").is_trusted is True


def test_trust_and_identity_of_a_device_persist_across_restart(paths):
    from handoff.db.repositories import DeviceRepository

    c = Core(paths, hostname="T")
    c.start()
    with c.db.session() as s:
        DeviceRepository(s).upsert("peer-1", "Aaron", "linux", last_ip="10.0.0.2", public_key="K1")
        DeviceRepository(s).set_trusted("peer-1", True)
    c.close()
    c = Core(paths, hostname="T")
    c.start()
    with c.db.session() as s:
        d = DeviceRepository(s).get_by_device_id("peer-1")
        assert (d.is_trusted, d.public_key, d.last_ip) == (True, "K1", "10.0.0.2")
    c.close()


def test_rediscovery_updates_the_ip_but_keeps_identity_and_trust(core):
    from handoff.db.repositories import DeviceRepository

    with core.db.session() as s:
        repo = DeviceRepository(s)
        repo.upsert("peer-1", "Aaron", "linux", last_ip="10.0.0.2", public_key="K1")
        repo.set_trusted("peer-1", True)
    with core.db.session() as s:
        d = DeviceRepository(s).upsert("peer-1", "Aaron", "linux", last_ip="10.0.0.99")
        assert (d.last_ip, d.public_key, d.is_trusted) == ("10.0.0.99", "K1", True)
        assert len(DeviceRepository(s).list_all()) == 1  # same device_id, same row


def test_trusting_an_unknown_device_is_an_error(core):
    from handoff.db.repositories import DeviceRepository

    with pytest.raises(HandOffError) as e, core.db.session() as s:
        DeviceRepository(s).set_trusted("ghost", True)
    assert e.value.code == "DEVICE_NOT_FOUND"


def test_audit_log_can_be_filtered(core):
    with core.db.session() as s:
        record_event(s, AuditEvent.FILE_IMPORTED, "a", file_id="f1")
        record_event(s, AuditEvent.TRANSFER_FAILED, "b", transfer_id="t1", device_id="d1")
    with core.db.session() as s:
        repo = AuditRepository(s)
        assert [r.message for r in repo.list(file_id="f1")] == ["a"]
        assert [r.message for r in repo.list(device_id="d1")] == ["b"]
        assert [r.message for r in repo.list(event_type="TRANSFER_FAILED")] == ["b"]


def test_a_failed_start_closes_the_database_and_reports_the_error(paths, monkeypatch):
    from handoff.errors import IdentityError

    disposed = []
    real = Core.start

    def bad_identity(_paths):
        raise IdentityError("damaged")

    monkeypatch.setattr("handoff.core.load_or_create_identity", bad_identity)
    c = Core(paths, hostname="T")
    orig_open = __import__("handoff.core", fromlist=["open_database"]).open_database

    def tracking_open(p):
        db = orig_open(p)
        orig_dispose = db.dispose
        db.dispose = lambda: (disposed.append(1), orig_dispose())[1]
        return db

    monkeypatch.setattr("handoff.core.open_database", tracking_open)
    with pytest.raises(IdentityError):
        c.start()
    assert disposed == [1] and real is Core.start

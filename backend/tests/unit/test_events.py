import json

from handoff.events import PROGRESS_INTERVAL_SECONDS, EventBus
from handoff.ipc.protocol import encode_event


class FakeHistory:
    def __init__(self):
        self.rows = {
            "tr_1": {"transfer_id": "tr_1", "status": "transferring", "bytes_transferred": 5}
        }

    def get(self, tid):
        row = self.rows.get(tid)
        return dict(row) if row else None


def bus_with_sink():
    bus, seen = EventBus(), []
    bus.history = FakeHistory()
    bus.subscribe(lambda name, data: seen.append((name, data)))
    return bus, seen


def test_a_pushed_event_has_no_id_so_the_ui_can_tell_it_from_a_response():
    line = encode_event("transfer.updated", {"transfer_id": "tr_1"})
    msg = json.loads(line)
    assert msg == {"event": "transfer.updated", "data": {"transfer_id": "tr_1"}}
    assert "id" not in msg and "\n" not in line


def test_publishes_the_transfers_current_state():
    bus, seen = bus_with_sink()
    bus.transfer_changed("tr_1")
    assert seen == [
        (
            "transfer.updated",
            {"transfer_id": "tr_1", "status": "transferring", "bytes_transferred": 5},
        )
    ]


def test_unknown_transfers_publish_nothing():
    bus, seen = bus_with_sink()
    bus.transfer_changed("nope")
    assert seen == []


def test_without_listeners_nothing_is_built():
    bus = EventBus()
    bus.history = FakeHistory()
    bus.transfer_changed("tr_1")  # must not even touch history
    assert not bus.active and not bus.progress_due("tr_1")


def test_without_history_nothing_is_published():
    bus, seen = EventBus(), []
    bus.subscribe(lambda n, d: seen.append(n))
    bus.transfer_changed("tr_1")
    assert seen == []


def test_progress_is_throttled_but_status_changes_never_are(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("handoff.events.time.monotonic", lambda: clock[0])
    bus, seen = bus_with_sink()
    for _ in range(5):
        bus.transfer_changed("tr_1", throttle=True)
    assert len(seen) == 1
    for _ in range(3):
        bus.transfer_changed("tr_1")  # a status change: always delivered
    assert len(seen) == 4
    clock[0] += PROGRESS_INTERVAL_SECONDS + 0.01
    bus.transfer_changed("tr_1", throttle=True)
    assert len(seen) == 5


def test_a_finished_transfer_resets_its_throttle(monkeypatch):
    monkeypatch.setattr("handoff.events.time.monotonic", lambda: 50.0)
    bus, seen = bus_with_sink()
    bus.transfer_changed("tr_1", throttle=True)
    bus.history.rows["tr_1"]["status"] = "completed"
    bus.transfer_changed("tr_1")
    assert "tr_1" not in bus._last_progress


def test_the_byte_count_never_goes_backwards():
    bus, seen = bus_with_sink()
    bus.transfer_changed("tr_1", bytes_transferred=3)
    bus.transfer_changed("tr_1", bytes_transferred=900)
    assert [d["bytes_transferred"] for _, d in seen] == [5, 900]


def test_a_broken_sink_does_not_stop_the_others_or_the_caller():
    bus, seen = bus_with_sink()

    def boom(name, data):
        raise RuntimeError("stdout closed")

    bus.subscribe(boom)
    bus.subscribe(lambda n, d: seen.append(("after", n)))
    bus.transfer_changed("tr_1")
    assert ("after", "transfer.updated") in seen


def test_a_failing_history_lookup_is_swallowed():
    bus, seen = bus_with_sink()

    class Broken:
        def get(self, tid):
            raise RuntimeError("db locked")

    bus.history = Broken()
    bus.transfer_changed("tr_1")
    assert seen == []

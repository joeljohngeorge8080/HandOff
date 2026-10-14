import json

import pytest
from sqlalchemy import select

from handoff.audit import AuditEvent
from handoff.core import Core
from handoff.db.models import AuditLog
from handoff.ipc.dispatcher import Dispatcher


class FakeSupervisor:
    def __init__(self):
        self.calls = []
        self._state = {"state": "off", "message": ""}

    def start(self):
        self.calls.append("start")
        self._state = {"state": "starting", "message": ""}
        return self._state

    def stop(self):
        self.calls.append("stop")
        self._state = {"state": "off", "message": ""}
        return self._state

    def status(self):
        return dict(self._state)


@pytest.fixture
def d(core):
    core.hand_control = FakeSupervisor()
    return Dispatcher(core)


def call(d, action, payload=None):
    msg = {"id": 1, "action": action, **({"payload": payload} if payload is not None else {})}
    return json.loads(d.handle_line(json.dumps(msg)))


def audit_types(core):
    with core.db.session() as s:
        return [r.event_type for r in s.scalars(select(AuditLog))]


def test_hand_control_is_off_by_default(d):
    assert call(d, "settings.get")["result"]["settings"]["hand_control_enabled"] is False
    assert call(d, "cv.status")["result"]["hand_control"]["state"] == "off"


def test_turning_it_on_starts_the_worker_and_is_audited(d, core):
    r = call(d, "settings.set", {"key": "hand_control_enabled", "value": True})
    assert r["result"]["settings"]["hand_control_enabled"] is True
    assert core.hand_control.calls == ["start"]
    assert AuditEvent.HAND_CONTROL_ENABLED in audit_types(core)


def test_turning_it_off_stops_the_worker_and_is_audited(d, core):
    call(d, "settings.set", {"key": "hand_control_enabled", "value": True})
    call(d, "settings.set", {"key": "hand_control_enabled", "value": False})
    assert core.hand_control.calls == ["start", "stop"]
    assert AuditEvent.HAND_CONTROL_DISABLED in audit_types(core)


def test_setting_the_same_value_twice_audits_once(d, core):
    for _ in range(2):
        call(d, "settings.set", {"key": "hand_control_enabled", "value": True})
    assert audit_types(core).count(AuditEvent.HAND_CONTROL_ENABLED) == 1


@pytest.mark.parametrize("bad", ["true", 1, 0, None, [], {"a": 1}])
def test_only_a_real_boolean_is_accepted(d, core, bad):
    r = call(d, "settings.set", {"key": "hand_control_enabled", "value": bad})
    assert r["error"]["code"] == "INVALID_REQUEST"
    assert core.hand_control.calls == []
    assert core.settings.hand_control_enabled() is False


def test_status_snapshot_carries_hand_control(d):
    assert call(d, "status.snapshot")["result"]["hand_control"] == {"state": "off", "message": ""}


def test_the_choice_survives_a_restart(paths):
    c = Core(paths, hostname="Test-Laptop")
    c.start()
    c.settings.set("hand_control_enabled", True)
    c.close()
    again = Core(paths, hostname="Test-Laptop")
    again.start()
    try:
        assert again.settings.hand_control_enabled() is True
    finally:
        again.close()


def test_core_close_stops_the_worker(paths):
    c = Core(paths, hostname="Test-Laptop")
    c.start()
    fake = FakeSupervisor()
    c.hand_control = fake
    c.close()
    assert fake.calls == ["stop"]

import threading

from handoff.history import PeriodicTask


def test_periodic_task_runs_immediately_repeats_and_stops():
    count = {"n": 0}
    ran_thrice = threading.Event()

    def work():
        count["n"] += 1
        if count["n"] >= 3:
            ran_thrice.set()

    t = PeriodicTask(work, 0.01, "test-task")
    t.start()
    assert ran_thrice.wait(timeout=5)
    t.stop()
    stopped_at = count["n"]
    threading.Event().wait(0.05)
    assert count["n"] == stopped_at  # no more runs after stop


def test_periodic_task_survives_a_failing_run_and_logs_it(caplog):
    calls = {"n": 0}
    twice = threading.Event()

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("first run fails")
        twice.set()

    t = PeriodicTask(flaky, 0.01, "flaky")
    t.start()
    assert twice.wait(timeout=5)
    t.stop()
    assert any("flaky" in r.getMessage() and r.exc_info for r in caplog.records)


def test_cleanup_ignores_a_corrupt_retention_value(core):
    from handoff.db.models import Setting

    with core.db.session() as s:
        row = s.get(Setting, "history_retention")
        row.value, row.value_type = "garbage", "str"
    assert core.history.cleanup() == 0

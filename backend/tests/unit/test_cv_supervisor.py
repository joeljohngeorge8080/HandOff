import json
import sys
import textwrap
import time

import pytest

from handoff import config
from handoff.cv.supervisor import CvSupervisor
from handoff.errors import HandOffError
from handoff.events import EventBus


def make(tmp_path, script=None, *, preflight=lambda: None, model_exists=True, release=None):
    bus = EventBus()
    seen: list[tuple[str, dict]] = []
    bus.subscribe(lambda n, d: seen.append((n, d)))
    model = tmp_path / "model.task"
    if model_exists:
        model.write_bytes(b"x")
    released = []
    cmd = (lambda _m: [sys.executable, "-c", textwrap.dedent(script)]) if script else None
    kwargs = {"command": cmd} if cmd else {}
    sup = CvSupervisor(
        bus,
        model=lambda: model,
        preflight=preflight,
        release_button=release or (lambda: released.append(1)),
        **kwargs,
    )
    return sup, seen, released


def wait_for(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


def states(seen):
    return [d["state"] for n, d in seen if n == "cv.status"]


WELL_BEHAVED = """
import sys, json
print(json.dumps({"event": "status", "state": "tracking", "message": ""}), flush=True)
g = {"event": "gesture_detected", "gesture": "pinch_closed", "confidence": 1.0}
print(json.dumps(g), flush=True)
sys.stdin.read()
"""


def test_a_well_behaved_worker_reports_status_and_gestures_and_stops_on_request(tmp_path):
    sup, seen, _ = make(tmp_path, WELL_BEHAVED)
    assert sup.start()["state"] == "starting"
    assert wait_for(lambda: any(n == "cv.event" for n, _ in seen))
    assert "tracking" in states(seen)
    assert sup.stop()["state"] == "off"
    assert sup.status()["state"] == "off"


def test_start_is_idempotent_while_running(tmp_path):
    sup, seen, _ = make(tmp_path, WELL_BEHAVED)
    sup.start()
    sup.start()
    assert states(seen).count("starting") == 1
    sup.stop()


def test_a_crashing_worker_becomes_an_error_releases_the_button_and_is_not_respawned(tmp_path):
    sup, seen, released = make(tmp_path, "import sys; sys.exit(3)")
    sup.start()
    assert wait_for(lambda: sup.status()["state"] == "error")
    assert released == [1]
    time.sleep(0.2)
    assert states(seen).count("starting") == 1  # no restart loop


def test_stopping_does_not_look_like_a_crash(tmp_path):
    sup, seen, released = make(tmp_path, WELL_BEHAVED)
    sup.start()
    assert wait_for(lambda: "tracking" in states(seen))
    sup.stop()
    time.sleep(0.2)
    assert "error" not in states(seen) and released == []


def test_a_worker_that_ignores_stdin_close_is_terminated(tmp_path):
    sup, _, _ = make(tmp_path, "import time; time.sleep(60)")
    sup.start()
    t0 = time.time()
    sup.stop()
    assert time.time() - t0 < 10 and sup.status()["state"] == "off"


def test_unsupported_session_is_refused_without_starting_a_process(tmp_path):
    def refuse():
        raise HandOffError("CV_UNSUPPORTED_SESSION", "Needs X11.")

    sup, seen, _ = make(tmp_path, WELL_BEHAVED, preflight=refuse)
    st = sup.start()
    assert st["state"] == "error" and st["code"] == "CV_UNSUPPORTED_SESSION"
    assert sup._proc is None


def test_missing_model_is_refused_and_nothing_is_downloaded(tmp_path):
    sup, _, _ = make(tmp_path, WELL_BEHAVED, model_exists=False)
    st = sup.start()
    assert st["state"] == "error" and st["code"] == "CV_UNAVAILABLE"
    assert not (tmp_path / "model.task").exists()


def test_a_missing_executable_is_an_error_not_an_exception(tmp_path):
    bus = EventBus()
    model = tmp_path / "m"
    model.write_bytes(b"x")
    sup = CvSupervisor(
        bus, command=lambda _m: ["/nonexistent/handoff-worker"], model=lambda: model,
        preflight=lambda: None,
    )  # fmt: skip
    assert sup.start()["state"] == "error"


# ----- hostile worker output -------------------------------------------------------------


def feed(tmp_path, lines, clock=None):
    sup, seen, _ = make(tmp_path)
    if clock:
        sup._clock = clock
    sup._proc = object()  # type: ignore[assignment]  # "running" for the status guard
    for line in lines:
        sup.handle_line(line)
    return seen


def test_non_canonical_and_pointer_events_are_never_forwarded(tmp_path):
    names = ["release", "drag_start", "drag_end", "grab", "pointer_click", "pointer_move",
             "selection_changed", "transfer.create", "files.delete", "bogus"]  # fmt: skip
    seen = feed(tmp_path, [json.dumps({"event": n, "paths": ["/etc/passwd"]}) for n in names])
    assert seen == []


@pytest.mark.parametrize(
    "line",
    [
        "not json", "[]", "42", "null", '{"event": 7}', "{}",
        '{"event": "gesture_detected"}',
        '{"event": "gesture_detected", "gesture": "../../x", "confidence": 1}',
        '{"event": "gesture_detected", "gesture": "ok", "confidence": 2}',
        '{"event": "gesture_detected", "gesture": "ok", "confidence": true}',
        '{"event": "gesture_detected", "gesture": "ok", "confidence": "high"}',
        '{"event": "direction_detected", "direction": "sideways"}',
        '{"event": "status", "state": "off"}',
        '{"event": "status", "state": "exploding"}',
        '{"event": "status", "state": "tracking", "message": 5}',
    ],
)  # fmt: skip
def test_malformed_worker_lines_are_dropped(tmp_path, line):
    assert feed(tmp_path, [line]) == []


def test_valid_events_are_forwarded_with_only_known_fields(tmp_path):
    seen = feed(
        tmp_path,
        [
            '{"event": "gesture_detected", "gesture": "pinch_closed", "confidence": 1, "x": "/"}',
            '{"event": "direction_detected", "direction": "right"}',
        ],
    )
    assert seen == [
        ("cv.event", {"event": "gesture_detected", "gesture": "pinch_closed", "confidence": 1.0}),
        ("cv.event", {"event": "direction_detected", "direction": "right"}),
    ]


def test_event_rate_is_capped_per_second_and_resumes_next_second(tmp_path):
    now = [100.0]
    line = '{"event": "direction_detected", "direction": "up"}'
    sup, seen, _ = make(tmp_path)
    sup._clock = lambda: now[0]
    sup._proc = object()  # type: ignore[assignment]
    for _ in range(config.CV_MAX_EVENTS_PER_SEC * 3):
        sup.handle_line(line)
    assert len(seen) == config.CV_MAX_EVENTS_PER_SEC
    now[0] += 1.1
    sup.handle_line(line)
    assert len(seen) == config.CV_MAX_EVENTS_PER_SEC + 1


def test_status_error_codes_are_restricted_and_messages_truncated(tmp_path):
    seen = feed(
        tmp_path,
        [json.dumps({"event": "status", "state": "error", "code": "EVIL", "message": "x" * 5000})],
    )
    data = seen[0][1]
    assert data["code"] == "CV_UNAVAILABLE" and len(data["message"]) == 200


def test_status_is_ignored_when_nothing_is_running(tmp_path):
    sup, seen, _ = make(tmp_path)
    sup.handle_line('{"event": "status", "state": "tracking"}')
    assert seen == []


def test_oversized_lines_are_dropped_without_killing_the_reader(tmp_path):
    script = f"""
    import sys, json
    sys.stdout.write("x" * {config.CV_MAX_LINE_BYTES * 3} + "\\n")
    print(json.dumps({{"event": "direction_detected", "direction": "left"}}), flush=True)
    sys.stdin.read()
    """
    sup, seen, _ = make(tmp_path, script)
    sup.start()
    assert wait_for(lambda: any(n == "cv.event" for n, _ in seen))
    sup.stop()


# ----- COPY gesture events (ADR-057) -----------------------------------------------------

PALM_WORKER = """
import sys, json
for ev in ("grab", "release", "grab", "bogus"):
    print(json.dumps({"event": ev, "paths": ["/etc/passwd"]}), flush=True)
sys.stdin.read()
"""


def test_grab_and_release_reach_the_core_handlers_and_are_never_forwarded_to_the_ui(tmp_path):
    sup, seen, _ = make(tmp_path, PALM_WORKER)
    calls: list[str] = []
    sup.on_grab = lambda: calls.append("grab")
    sup.on_release = lambda: calls.append("release")
    sup.start()
    assert wait_for(lambda: len(calls) == 3)
    assert sorted(calls) == ["grab", "grab", "release"]
    assert not [n for n, _ in seen if n == "cv.event"]  # nothing, and no worker paths, forwarded
    sup.stop()


def test_a_failing_handler_does_not_take_the_reader_down(tmp_path):
    sup, seen, _ = make(tmp_path, PALM_WORKER)
    done = []

    def boom():
        done.append(1)
        raise RuntimeError("handler bug")

    sup.on_grab = boom
    sup.start()
    assert wait_for(lambda: len(done) == 2)
    assert sup.status()["state"] in ("starting", "tracking", "no_hand")
    sup.stop()


def test_palm_events_are_ignored_when_no_handler_is_installed(tmp_path):
    sup, seen, _ = make(tmp_path, PALM_WORKER)
    sup.start()
    time.sleep(0.3)
    assert sup.status()["state"] != "error"
    sup.stop()

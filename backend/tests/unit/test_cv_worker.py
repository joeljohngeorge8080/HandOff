import io
import json
import threading
import time
from dataclasses import dataclass

import pytest

from handoff import config
from handoff.cv import pointer as pointer_mod
from handoff.cv import tracker as tracker_mod
from handoff.cv import worker
from handoff.cv.gestures import HandSample
from handoff.cv.samples import Detection, SampleBuilder, analyze
from handoff.errors import HandOffError


@dataclass
class P:
    x: float
    y: float
    z: float = 0.0


def hand(gap: float = 0.02, size: float = 0.2) -> list[P]:
    """21 landmarks on a 1:1 frame: wrist at (0.5,0.8), middle MCP `size` above it."""
    pts = [P(0.5, 0.5) for _ in range(21)]
    pts[0] = P(0.5, 0.8)
    pts[9] = P(0.5, 0.8 - size)
    pts[5] = P(0.45, 0.6)
    pts[4] = P(0.5, 0.5)
    pts[8] = P(0.5 + gap, 0.5)
    return pts


class FakePointer:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def screen_size(self):
        return (1000, 800)

    def position(self):
        return (500, 400)

    def move(self, x, y):
        self.calls.append(("move", x, y))

    def down(self):
        self.calls.append(("down",))

    def up(self):
        self.calls.append(("up",))

    def escape(self):
        self.calls.append(("esc",))

    def copy(self):
        self.calls.append(("copy",))


def lines(buf: io.StringIO) -> list[dict]:
    return [json.loads(x) for x in buf.getvalue().splitlines()]


# ----- landmark analysis ------------------------------------------------------------------


def test_pinch_ratio_is_gap_over_hand_size():
    det = analyze(hand(gap=0.04, size=0.2), (100, 100))
    assert det is not None and det.pinch == pytest.approx(0.2)


def test_closed_and_open_hands_land_on_either_side_of_the_thresholds():
    closed = analyze(hand(gap=0.02), (100, 100))
    opened = analyze(hand(gap=0.1), (100, 100))
    assert closed.pinch < config.CV_PINCH_ON and opened.pinch > config.CV_PINCH_OFF


def test_the_anchor_does_not_move_when_the_fingers_close():
    a = analyze(hand(gap=0.02), (100, 100))
    b = analyze(hand(gap=0.2), (100, 100))
    assert (a.nx, a.ny) == (b.nx, b.ny)


def test_pinch_is_independent_of_how_far_the_hand_is_from_the_camera():
    near = analyze(hand(gap=0.04, size=0.2), (100, 100))
    far = analyze(hand(gap=0.02, size=0.1), (100, 100))
    assert near.pinch == pytest.approx(far.pinch)


def test_world_landmarks_are_used_when_asked():
    lm = hand(gap=0.2)  # 2D says wide open
    world = [P(0, 0, 0) for _ in range(21)]
    world[9] = P(0, 0.1, 0)
    world[4], world[8] = P(0, 0, 0), P(0.01, 0, 0)  # 3D says nearly touching
    assert analyze(lm, (100, 100), world, use_world=True).pinch == pytest.approx(0.1)
    assert analyze(lm, (100, 100), world, use_world=False).pinch > 0.5


@pytest.mark.parametrize("bad", [[], hand()[:20]])
def test_short_landmark_lists_are_rejected(bad):
    assert analyze(bad, (100, 100)) is None


def test_zero_size_or_nan_hands_are_rejected():
    flat = hand()
    flat[9] = P(flat[0].x, flat[0].y)
    assert analyze(flat, (100, 100)) is None
    nan = hand()
    nan[5] = P(float("nan"), 0.5)
    assert analyze(nan, (100, 100)) is None


def test_sample_builder_starts_at_the_real_cursor_and_flags_the_first_frame_and_lifts():
    b = SampleBuilder((1000, 800), lambda: (300.0, 200.0))
    det = Detection(0.5, 0.5, 0.5)
    idle = b.build(0.0, det)  # the pointing pose must hold briefly before the cursor follows
    assert not idle.active and (idle.x, idle.y) == (500.0, 400.0)
    first = b.build(config.CV_POINT_ON_SECONDS, det)
    assert first.active and first.reacquired and (first.x, first.y) == (300.0, 200.0)
    assert not b.build(config.CV_POINT_ON_SECONDS + 0.02, det).reacquired
    assert b.build(5.0, det).reacquired


def test_sample_builder_moves_the_cursor_by_hand_movement_not_hand_position():
    b = SampleBuilder((1000, 800), lambda: (500.0, 400.0))
    b.build(0.0, Detection(0.2, 0.2, 0.5))
    moved = [b.build(i / 60, Detection(0.2 + i * 0.01, 0.2, 0.5)) for i in range(1, 30)]
    assert moved[-1].active
    assert moved[-1].x > 500 and abs(moved[-1].y - 400) < 1
    # the hand is still in the left half of the frame, yet the cursor has crossed the middle


# ----- emitter ---------------------------------------------------------------------------


def test_status_lines_are_single_line_json_and_repeats_are_throttled():
    buf = io.StringIO()
    e = worker.Emitter(buf)
    e.status("tracking", now=0.0)
    e.status("tracking", now=0.1)
    e.status("tracking", now=5.0)
    e.status("no_hand", now=5.1)
    assert [x["state"] for x in lines(buf)] == ["tracking", "tracking", "no_hand"]


def test_a_closed_pipe_never_raises():
    class Closed:
        def write(self, _s):
            raise BrokenPipeError

        def flush(self):
            pass

    worker.Emitter(Closed()).gesture("pinch_closed")  # type: ignore[arg-type]


# ----- controller ------------------------------------------------------------------------


def sample(t, pinch, x=10.0):
    return HandSample(t=t, x=x, y=10.0, pinch=pinch)


def test_cancel_presses_escape_before_releasing_and_shutdown_never_leaves_a_button_down():
    p, buf = FakePointer(), io.StringIO()
    c = worker.Controller(p, worker.Emitter(buf))
    c.tick(0.0, sample(0.0, 0.1, x=0))
    for i in range(1, 40):
        c.tick(i * 0.01, sample(i * 0.01, 0.1, x=i * 30))
    c.tick(9.0, None)
    names = [x[0] for x in p.calls if x[0] != "move"]
    assert names == ["down", "esc", "up"]
    assert not c.machine.pressed


def test_shutdown_releases_a_held_button():
    p = FakePointer()
    c = worker.Controller(p, worker.Emitter(io.StringIO()))
    c.tick(0.0, sample(0.0, 0.1))
    c.shutdown()
    assert p.calls[-1] == ("up",)
    c.shutdown()
    assert p.calls.count(("up",)) == 1


def test_gestures_are_reported_with_canonical_names():
    buf = io.StringIO()
    c = worker.Controller(FakePointer(), worker.Emitter(buf))
    c.tick(0.0, sample(0.0, 0.1))
    ev = [x for x in lines(buf) if x["event"] == "gesture_detected"]
    assert ev == [{"event": "gesture_detected", "gesture": "pinch_closed", "confidence": 1.0}]


# ----- COPY gesture (ADR-057) --------------------------------------------------------------


def palm(t, pose, pinch=0.6):
    return HandSample(t=t, x=10.0, y=10.0, pinch=pinch, pose=pose, active=False)


def palm_controller():
    p, buf, slept = FakePointer(), io.StringIO(), []
    return worker.Controller(p, worker.Emitter(buf), sleep=slept.append), p, buf, slept


def hold(c, pose, start, seconds, pinch=0.6):
    t = start
    while t < start + seconds:
        c.tick(t, palm(t, pose, pinch))
        t += 0.02
    return t


def test_grab_copies_the_selection_then_tells_the_core_after_the_clipboard_settles():
    c, p, buf, slept = palm_controller()
    t = hold(c, "open", 0.0, 0.5)
    hold(c, "fist", t, 0.6)
    assert p.calls == [("copy",)]
    assert slept == [config.CV_COPY_SETTLE_SECONDS]
    assert [x["event"] for x in lines(buf)] == ["gesture_detected", "grab"]
    assert lines(buf)[0]["gesture"] == "palm_grab"  # the UI animates at once, before the copy


def test_release_tells_the_core_and_presses_no_keys():
    c, p, buf, _ = palm_controller()
    t = hold(c, "open", 0.0, 0.5)
    t = hold(c, "fist", t, 0.6)
    hold(c, "open", t, 0.6)
    assert [x.get("gesture", x["event"]) for x in lines(buf)] == [
        "palm_grab", "grab", "palm_release", "release",
    ]  # fmt: skip
    assert p.calls == [("copy",)]


def test_a_pointing_hand_never_copies_or_sends():
    c, p, buf, _ = palm_controller()
    for i in range(100):
        c.tick(i * 0.02, HandSample(t=i * 0.02, x=10.0, y=10.0, pinch=0.6))
    assert ("copy",) not in p.calls and lines(buf) == []


def test_a_fist_while_the_mouse_button_is_down_never_presses_ctrl_c():
    c, p, buf, _ = palm_controller()
    c.tick(0.0, sample(0.0, 0.1))  # pinch held: a drag in progress
    t = hold(c, "open", 0.02, 0.5, pinch=0.1)
    hold(c, "fist", t, 0.6, pinch=0.1)
    assert c.machine.pressed
    assert ("copy",) not in p.calls
    assert all(x["event"] != "grab" for x in lines(buf))


# ----- run() -----------------------------------------------------------------------------


class BlockingStdin:
    def __init__(self) -> None:
        self.closed = threading.Event()

    def read(self):
        self.closed.wait(5)
        return ""


def test_a_missing_model_reports_cv_unavailable_and_never_downloads(tmp_path):
    buf = io.StringIO()
    rc = worker.run(tmp_path / "nope.task", buf, io.StringIO(), pointer=FakePointer())
    last = lines(buf)[-1]
    assert rc == 2 and last["state"] == "error" and last["code"] == "CV_UNAVAILABLE"
    assert not (tmp_path / "nope.task").exists()


def test_run_presses_on_a_pinch_and_releases_when_stdin_closes(tmp_path, monkeypatch):
    frames = iter([(time.perf_counter(), Detection(0.5, 0.5, 0.05))])

    class FakeTracker:
        def __init__(self, _path):
            pass

        def read(self):
            try:
                t, d = next(frames)
                return time.perf_counter(), d
            except StopIteration:
                time.sleep(0.005)
                return time.perf_counter(), Detection(0.5, 0.5, 0.05)

        def close(self):
            pass

    monkeypatch.setattr(tracker_mod, "HandTracker", FakeTracker)
    p, buf, stdin = FakePointer(), io.StringIO(), BlockingStdin()
    done = {}
    t = threading.Thread(target=lambda: done.update(rc=worker.run(tmp_path / "m", buf, stdin, p)))
    t.start()
    deadline = time.time() + 3
    while ("down",) not in p.calls and time.time() < deadline:
        time.sleep(0.01)
    stdin.closed.set()
    t.join(5)
    assert done["rc"] == 0
    assert ("down",) in p.calls and p.calls[-1] == ("up",)
    states = {x["state"] for x in lines(buf) if x["event"] == "status"}
    assert {"starting", "tracking"} <= states


# ----- pointer ---------------------------------------------------------------------------


def test_native_wayland_without_x11_is_refused(monkeypatch):
    monkeypatch.setattr(pointer_mod.sys, "platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    with pytest.raises(HandOffError) as e:
        pointer_mod.check_session()
    assert e.value.code == "CV_UNSUPPORTED_SESSION"


def test_x11_session_is_accepted(monkeypatch):
    monkeypatch.setattr(pointer_mod.sys, "platform", "linux")
    monkeypatch.setenv("DISPLAY", ":0")
    pointer_mod.check_session()


def test_pyautogui_loader_blocks_mouseinfo_and_survives_a_sys_exit_on_import(monkeypatch):
    import builtins
    import sys

    monkeypatch.delitem(sys.modules, "pyautogui", raising=False)
    monkeypatch.delitem(sys.modules, "mouseinfo", raising=False)
    real_import = builtins.__import__

    def exploding(name, *a, **k):
        if name == "pyautogui":
            raise SystemExit  # what mouseinfo does without tkinter
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", exploding)
    with pytest.raises(HandOffError) as e:
        pointer_mod.load_pyautogui()
    assert e.value.code == "CV_UNAVAILABLE"
    assert sys.modules["mouseinfo"] is None  # MouseInfo can no longer exit the process

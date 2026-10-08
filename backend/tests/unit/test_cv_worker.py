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

    def move(self, x, y):
        self.calls.append(("move", x, y))

    def down(self):
        self.calls.append(("down",))

    def up(self):
        self.calls.append(("up",))

    def escape(self):
        self.calls.append(("esc",))


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


def test_sample_builder_marks_the_first_frame_and_returns_after_a_gap_as_reacquired():
    b = SampleBuilder((1000, 800))
    det = Detection(0.5, 0.5, 0.5)
    assert b.build(0.0, det).reacquired
    assert not b.build(0.05, det).reacquired
    assert b.build(5.0, det).reacquired


def test_sample_builder_maps_to_screen_pixels():
    b = SampleBuilder((1000, 800))
    s = b.build(0.0, Detection(0.5, 0.5, 0.5))
    assert (s.x, s.y) == pytest.approx((499.5, 399.5))


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

"""COPY gesture: open palm -> closed palm = grab; closed -> open palm = release (ADR-057)."""

from handoff import config
from handoff.cv.palm import GRAB, RELEASE, PalmMachine
from handoff.cv.samples import POSE_FIST, POSE_OPEN, POSE_OTHER, POSE_POINT

HOLD = config.CV_PALM_HOLD_SECONDS
DT = 0.02


def run(m, script, t0=0.0):
    """script: list of (pose|None, seconds). Returns (events, end time)."""
    out, t = [], t0
    for pose, seconds in script:
        end = t + seconds
        while t < end:
            out += m.step(t, pose)
            t += DT
    return out, t


def test_open_then_closed_palm_grabs_once():
    out, _ = run(PalmMachine(), [(POSE_OPEN, 0.5), (POSE_FIST, 1.0)])
    assert out == [GRAB]


def test_closed_then_open_palm_after_a_grab_releases_once():
    out, _ = run(PalmMachine(), [(POSE_OPEN, 0.5), (POSE_FIST, 1.0), (POSE_OPEN, 1.0)])
    assert out == [GRAB, RELEASE]


def test_a_fist_with_no_open_palm_before_it_never_grabs():
    out, _ = run(PalmMachine(), [(POSE_POINT, 0.5), (POSE_FIST, 2.0)])
    assert out == []


def test_a_brief_flash_of_open_palm_or_fist_is_ignored():
    short = HOLD / 3
    out, _ = run(
        PalmMachine(), [(POSE_OPEN, short), (POSE_FIST, 1.0), (POSE_OPEN, short), (POSE_FIST, 1.0)]
    )
    assert out == []


def test_the_fist_must_follow_the_open_palm_promptly():
    out, _ = run(
        PalmMachine(),
        [(POSE_OPEN, 0.5), (POSE_POINT, config.CV_GRAB_WINDOW_SECONDS + 1), (POSE_FIST, 1.0)],
    )
    assert out == []


def test_transition_frames_between_palm_and_fist_do_not_break_the_gesture():
    out, _ = run(PalmMachine(), [(POSE_OPEN, 0.5), (POSE_OTHER, 0.1), (POSE_FIST, 0.6)])
    assert out == [GRAB]


def test_the_hand_may_leave_the_camera_view_while_holding_and_still_release():
    out, _ = run(PalmMachine(), [(POSE_OPEN, 0.5), (POSE_FIST, 0.6), (None, 3.0), (POSE_OPEN, 0.6)])
    assert out == [GRAB, RELEASE]


def test_release_without_a_grab_does_nothing():
    out, _ = run(PalmMachine(), [(POSE_FIST, 0.5), (POSE_OPEN, 2.0)])
    assert out == []


def test_a_grab_that_is_never_released_expires():
    m = PalmMachine()
    out, t = run(m, [(POSE_OPEN, 0.5), (POSE_FIST, 0.6), (None, config.CV_HOLD_MAX_SECONDS + 1)])
    assert out == [GRAB]
    out, _ = run(m, [(POSE_OPEN, 1.0)], t0=t)
    assert out == []  # the expired grab is gone: this open palm releases nothing


def test_a_second_grab_is_blocked_during_the_cooldown_then_allowed():
    m = PalmMachine()
    out, t = run(m, [(POSE_OPEN, 0.5), (POSE_FIST, 0.6), (POSE_OPEN, 0.6)])
    assert out == [GRAB, RELEASE]
    out, t = run(m, [(POSE_FIST, 0.6)], t0=t)  # right away: inside the cooldown
    assert out == []
    out, _ = run(m, [(POSE_OPEN, 0.6), (POSE_FIST, 0.6)], t0=t + config.CV_COPY_COOLDOWN_SECONDS)
    assert out == [GRAB]


def test_time_going_backwards_or_nan_never_raises():
    m = PalmMachine()
    assert m.step(5.0, POSE_OPEN) == []
    assert m.step(1.0, POSE_FIST) == []
    assert m.step(float("nan"), POSE_FIST) == []

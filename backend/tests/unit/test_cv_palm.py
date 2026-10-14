"""COPY gesture: open -> closed palm = grab; closed -> open palm = release (ADR-057, ADR-058).

The machine does not know which laptop holds the grab: a release is reported for any stable fist
that opens. The core decides what it means (cancel here, or claim from the peer)."""

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


def test_a_fist_that_arrives_from_elsewhere_and_opens_is_a_release_without_a_grab_here():
    # laptop 2: the closed hand enters the camera's view and opens (ADR-058)
    out, _ = run(PalmMachine(), [(None, 1.0), (POSE_FIST, 0.5), (POSE_OPEN, 1.0)])
    assert out == [RELEASE]


def test_an_open_palm_with_no_fist_before_it_releases_nothing():
    out, _ = run(PalmMachine(), [(POSE_POINT, 0.5), (POSE_OPEN, 2.0)])
    assert out == []


def test_a_brief_fist_flash_before_opening_is_not_a_release():
    out, _ = run(PalmMachine(), [(POSE_FIST, HOLD / 3), (POSE_OPEN, 1.0)])
    assert out == []


def test_a_fist_that_was_last_seen_long_ago_cannot_release():
    m = PalmMachine()
    out, t = run(m, [(POSE_OPEN, 0.5), (POSE_FIST, 0.6), (None, config.CV_HOLD_MAX_SECONDS + 1)])
    assert out == [GRAB]
    out, _ = run(m, [(POSE_OPEN, 1.0)], t0=t)
    assert out == []  # too much time passed since the closed hand: nothing to release


def test_a_second_grab_is_blocked_during_the_cooldown_then_allowed():
    m = PalmMachine()
    out, t = run(m, [(POSE_OPEN, 0.5), (POSE_FIST, 0.6), (POSE_OPEN, 0.6)])
    assert out == [GRAB, RELEASE]
    out, t = run(m, [(POSE_FIST, 0.6), (POSE_OPEN, 0.6)], t0=t)  # right away: inside the cooldown
    assert out == []
    out, _ = run(m, [(POSE_OPEN, 0.6), (POSE_FIST, 0.6)], t0=t + config.CV_COPY_COOLDOWN_SECONDS)
    assert out == [GRAB]


def test_time_going_backwards_or_nan_never_raises():
    m = PalmMachine()
    assert m.step(5.0, POSE_OPEN) == []
    assert m.step(1.0, POSE_FIST) == []
    assert m.step(float("nan"), POSE_FIST) == []

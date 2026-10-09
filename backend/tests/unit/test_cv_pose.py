"""Pose classification: only an index finger pointed (or pinched to the thumb) moves the cursor."""

import math
from dataclasses import dataclass

import pytest

from handoff import config
from handoff.cv.samples import (
    POSE_FIST,
    POSE_OPEN,
    POSE_OTHER,
    POSE_POINT,
    Detection,
    SampleBuilder,
    analyze,
)

WRIST = (0.5, 0.9)
# (mcp, pip, tip) for index, middle, ring, pinky, fanned above the wrist.
FINGERS = {
    "index": (5, 6, 8, -0.10),
    "middle": (9, 10, 12, 0.0),
    "ring": (13, 14, 16, 0.05),
    "pinky": (17, 18, 20, 0.10),
}


@dataclass
class P:
    x: float
    y: float
    z: float = 0.0


def pose_hand(extended: set[str], pinch_gap: float = 0.15) -> list[P]:
    """21 landmarks on a square frame. Extended fingers reach far from the wrist, curled ones
    fold back toward it (tip nearer the wrist than the middle joint)."""
    pts = [P(*WRIST) for _ in range(21)]
    for name, (mcp, pip, tip, dx) in FINGERS.items():
        x = 0.5 + dx
        pts[mcp] = P(x, 0.65)
        pts[pip] = P(x, 0.50)
        pts[tip] = P(x, 0.25) if name in extended else P(x, 0.62)
    pts[4] = P(pts[8].x - pinch_gap, pts[8].y)  # thumb tip
    return pts


def pose_of(extended, pinch_gap=0.15):
    d = analyze(pose_hand(extended, pinch_gap), (640, 640))
    assert d is not None
    return d.pose


def test_only_the_index_extended_is_pointing():
    assert pose_of({"index"}) == POSE_POINT


def test_an_open_hand_is_a_palm_and_does_not_point():
    assert pose_of({"index", "middle", "ring", "pinky"}) == POSE_OPEN


def test_a_closed_hand_is_a_fist():
    assert pose_of(set(), pinch_gap=0.3) == POSE_FIST


@pytest.mark.parametrize(
    "extended", [{"index", "middle"}, {"middle"}, {"pinky"}, {"middle", "ring"}]
)
def test_other_finger_combinations_never_point(extended):
    assert pose_of(extended) in (POSE_OTHER, POSE_OPEN)
    assert pose_of(extended) != POSE_POINT


def test_index_and_thumb_touching_while_the_others_are_curled_still_points():
    # the index curls toward the thumb in a pinch click; the cursor must not drop out
    hand = pose_hand({"index"})
    hand[8] = P(0.4, 0.40)  # index tip bent back toward the palm
    hand[4] = P(0.41, 0.40)  # thumb touching it
    d = analyze(hand, (640, 640))
    assert d is not None and d.pose == POSE_POINT and d.pinch < config.CV_PINCH_ON


def test_a_real_fist_with_the_thumb_over_the_fingers_is_not_a_pinch_point():
    hand = pose_hand(set())
    hand[4] = P(hand[8].x, hand[8].y)  # thumb tip on the index tip, index fully folded
    d = analyze(hand, (640, 640))
    assert d is not None and d.pose == POSE_FIST


def test_degenerate_geometry_is_not_a_pose():
    hand = pose_hand({"index"})
    hand[10] = P(*WRIST)  # a joint on top of the wrist: ratio would divide by zero
    d = analyze(hand, (640, 640))
    assert d is None or d.pose in (POSE_OTHER, POSE_POINT, POSE_OPEN, POSE_FIST)
    assert d is None or math.isfinite(d.pinch)


# ----- the builder gates movement on a stable pointing pose ---------------------------------


def builder():
    return SampleBuilder((1920, 1080), lambda: (960.0, 540.0))


def det(t_x, pose, pinch=0.6):
    return Detection(nx=t_x, ny=0.5, pinch=pinch, pose=pose)


def feed(b, poses, start=0.0, dt=0.016, x0=0.3, dx=0.01):
    out = []
    for i, pose in enumerate(poses):
        t = start + i * dt
        out.append(b.build(t, det(x0 + i * dx, pose)))
    return out


def test_the_cursor_does_not_move_while_the_hand_is_not_pointing():
    b = builder()
    out = feed(b, [POSE_OPEN] * 30)
    assert not any(s.active for s in out)
    assert len({(s.x, s.y) for s in out}) == 1


def test_pointing_moves_the_cursor_after_a_short_hold():
    b = builder()
    out = feed(b, [POSE_POINT] * 30)
    assert out[-1].active and not out[0].active
    assert out[-1].x > out[10].x


def test_a_single_dropped_frame_does_not_stop_the_cursor():
    b = builder()
    out = feed(b, [POSE_POINT] * 10 + [POSE_OTHER] + [POSE_POINT] * 10)
    assert all(s.active for s in out[5:])


def test_leaving_the_pose_stops_the_cursor_and_returning_does_not_jump_it():
    b = builder()
    feed(b, [POSE_POINT] * 20)
    gap = feed(b, [POSE_FIST] * 20, start=0.4, x0=0.9)  # hand travels while not pointing
    assert not gap[-1].active
    back = feed(b, [POSE_POINT] * 20, start=0.8, x0=0.1)
    first_active = next(s for s in back if s.active)
    assert first_active.reacquired  # resumes from the real cursor, not from the hand


def test_an_active_pinch_keeps_following_even_if_the_pose_flickers():
    b = builder()
    feed(b, [POSE_POINT] * 20)
    out = [
        b.build(0.4 + i * 0.016, det(0.5 + i * 0.01, POSE_OTHER, pinch=config.CV_PINCH_ON / 2))
        for i in range(20)
    ]
    assert all(s.active for s in out)

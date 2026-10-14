"""Pose classification: only an index finger pointed (or pinched to the thumb) moves the cursor."""

import json
import math
from dataclasses import dataclass
from pathlib import Path

import pytest

from handoff import config
from handoff.cv.samples import (
    POSE_FIST,
    POSE_OPEN,
    POSE_OTHER,
    POSE_POINT,
    POSE_SCROLL,
    Detection,
    SampleBuilder,
    analyze,
)

WRIST = (0.5, 0.9)
# (mcp, pip, dip, tip) for index, middle, ring, pinky, fanned above the wrist.
FINGERS = {
    "index": (5, 6, 7, 8, -0.10),
    "middle": (9, 10, 11, 12, 0.0),
    "ring": (13, 14, 15, 16, 0.05),
    "pinky": (17, 18, 19, 20, 0.10),
}


@dataclass
class P:
    x: float
    y: float
    z: float = 0.0


def pose_hand(extended: set[str], pinch_gap: float = 0.15) -> list[P]:
    """21 landmarks on a square frame. Extended fingers are straight lines away from the wrist;
    curled ones fold back at the middle joint (~180 degrees of bend)."""
    pts = [P(*WRIST) for _ in range(21)]
    for name, (mcp, pip, dip, tip, dx) in FINGERS.items():
        x = 0.5 + dx
        pts[mcp] = P(x, 0.65)
        pts[pip] = P(x, 0.50)
        if name in extended:
            pts[dip], pts[tip] = P(x, 0.37), P(x, 0.25)
        else:
            pts[dip], pts[tip] = P(x, 0.56), P(x, 0.62)
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
    assert pose_of(extended) in (POSE_OTHER, POSE_OPEN, POSE_SCROLL)
    assert pose_of(extended) != POSE_POINT


def test_index_and_thumb_touching_while_the_others_are_curled_still_points():
    # the index curls toward the thumb in a pinch click; the cursor must not drop out
    hand = pose_hand(set())
    # the index bends ~110 degrees into an "O" (pip -> dip 50 degrees, dip -> tip 40 more), far
    # less than the folded fingers; the thumb tip touches it
    hand[7] = P(0.4 + 0.0766, 0.5 - 0.0643)
    hand[8] = P(0.4 + 0.1566, 0.5 - 0.0643)
    hand[4] = P(0.4 + 0.1666, 0.5 - 0.0643)
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


def test_a_hand_closing_into_a_fist_never_starts_pointing():
    # Measured on a real hand: open -> two "point" frames with the thumb on the curled index
    # (pinch ~0.14) -> fist. Pointing there would press the mouse button in the middle of a grab.
    b = builder()
    t = 0.0
    for pose, n in [(POSE_OPEN, 15), (POSE_POINT, 3), (POSE_FIST, 15), (POSE_POINT, 3)]:
        for _ in range(n):
            s = b.build(t, det(0.5, pose, pinch=0.14))
            assert not s.active
            t += 0.033


def test_pointing_starts_again_once_the_palm_is_gone():
    b = builder()
    feed(b, [POSE_OPEN] * 10)
    out = feed(b, [POSE_POINT] * 40, start=0.2)
    assert out[-1].active
    first = next(i for i, s in enumerate(out) if s.active)
    assert first * 0.016 >= config.CV_POINT_AFTER_PALM_SECONDS - 0.2 - 0.016


def test_a_pinch_click_through_a_fist_like_frame_still_clicks():
    # measured: a real pinch click showed one "fist" frame as the index curled
    b = builder()
    feed(b, [POSE_POINT] * 30, dt=0.033)
    t = 30 * 0.033
    out = []
    for pose, pinch in [(POSE_FIST, 0.32), (POSE_POINT, 0.15), (POSE_POINT, 0.14)]:
        out.append(b.build(t, det(0.6, pose, pinch=pinch)))
        t += 0.033
    assert all(s.active for s in out) and out[-1].pinch < config.CV_PINCH_ON


# ----- real hands (ADR-064) ------------------------------------------------------------------
# Frames recorded from a webcam (landmarks only, no image): a deliberate fist seen knuckles-first,
# which the old 2D tip-to-wrist ratio read as "point"; the two-finger scroll pose; an open palm.

REAL = json.loads((Path(__file__).parents[1] / "fixtures" / "hand_landmarks.json").read_text())


@pytest.mark.parametrize(
    ("name", "pose"),
    [("fist_front", POSE_FIST), ("scroll", POSE_SCROLL), ("open", POSE_OPEN)],
)
def test_real_hands_are_classified_by_joint_bend(name, pose):
    d = analyze([P(*p) for p in REAL[name]], (640, 480))
    assert d is not None and d.pose == pose


def test_two_fingers_together_scroll_but_spread_apart_do_not():
    together = pose_hand({"index", "middle"})
    for i in (5, 6, 7, 8):
        together[i] = P(together[i].x + 0.07, together[i].y)  # index beside the middle finger
    assert analyze(together, (640, 640)).pose == POSE_SCROLL
    spread = pose_hand({"index", "middle"})
    for i in (6, 7, 8):
        spread[i] = P(spread[i].x - 0.12 * (i - 5), spread[i].y)  # a V: tips far apart
    assert analyze(spread, (640, 640)).pose != POSE_SCROLL


# ----- scroll gate ---------------------------------------------------------------------------


def scroll_builder(enabled=True):
    return SampleBuilder((1920, 1080), lambda: (960.0, 540.0), scroll=enabled)


def sdet(pose, tips_y=0.5):
    return Detection(nx=0.5, ny=0.5, pinch=0.8, pose=pose, tips_y=tips_y)


def test_scroll_is_off_unless_switched_on():
    b = scroll_builder(enabled=False)
    out = [b.build(i * 0.033, sdet(POSE_SCROLL)) for i in range(30)]
    assert all(s.scroll_y is None for s in out)


def test_the_scroll_pose_scrolls_after_a_short_hold_and_never_moves_the_cursor():
    b = scroll_builder()
    out = [b.build(i * 0.033, sdet(POSE_SCROLL, 0.5 + i * 0.01)) for i in range(30)]
    assert out[0].scroll_y is None and out[-1].scroll_y is not None
    assert not any(s.active for s in out)
    first = next(s for s in out if s.scroll_y is not None)
    assert first.reacquired  # the start is a reference, not a jump to scroll


def test_curling_the_two_fingers_keeps_scrolling_and_is_not_a_fist():
    b = scroll_builder()
    t = 0.0
    for _ in range(10):
        b.build(t, sdet(POSE_SCROLL))
        t += 0.033
    curled = [b.build(t + i * 0.033, sdet(POSE_FIST, 0.6)) for i in range(15)]
    assert all(s.scroll_y is not None and s.pose == POSE_SCROLL for s in curled)


def test_an_open_palm_ends_scrolling():
    b = scroll_builder()
    for i in range(10):
        b.build(i * 0.033, sdet(POSE_SCROLL))
    out = [b.build(0.33 + i * 0.033, sdet(POSE_OPEN)) for i in range(15)]
    assert out[-1].scroll_y is None and out[-1].pose == POSE_OPEN


# ----- fingertip steering (ADR-065) ----------------------------------------------------------

HAND = 0.2  # hand size in camera-widths for these detections


def tdet(tip_x, palm_x=0.5, pinch=0.9, hand=HAND, pose=POSE_POINT):
    return Detection(
        nx=palm_x, ny=0.5, pinch=pinch, pose=pose, tip_x=tip_x, tip_y=0.3,
        per_x=1 / hand, per_y=1 / hand,
    )  # fmt: skip


def steer(frames, dt=0.033):
    """frames: Detections fed at camera rate after a settled pointing start; cursor x moved."""
    b = builder()
    for i in range(10):
        b.build(i * dt, frames[0])
    start = b.build(10 * dt, frames[0]).x
    out = [b.build((11 + i) * dt, d) for i, d in enumerate(frames)]
    return out[-1].x - start


def test_moving_only_the_index_finger_moves_the_cursor():
    moved = steer([tdet(0.5 + i * 0.004) for i in range(20)])  # palm still, fingertip moves
    assert moved > 20


def test_a_quick_pinch_does_not_drag_the_cursor_off_its_target():
    # the fingertip travels 0.08 camera-widths toward the thumb in 4 frames while the pinch closes
    pinches = [0.9, 0.7, 0.5, 0.3, 0.15] + [0.15] * 5
    tips = [0.5, 0.48, 0.46, 0.44, 0.42] + [0.42] * 5
    moved = steer([tdet(x, pinch=p) for x, p in zip(tips, pinches, strict=True)])
    assert abs(moved) < 3


def test_a_closed_pinch_drags_with_the_palm():
    moved = steer([tdet(0.42, palm_x=0.5 + i * 0.005, pinch=0.15) for i in range(20)])
    assert moved > 20


def test_the_same_physical_movement_moves_the_cursor_the_same_near_or_far():
    # a hand half as big in the image (twice as far) moves half as many camera units
    near = steer([tdet(0.5 + i * 0.004, hand=0.2) for i in range(20)])
    far = steer([tdet(0.5 + i * 0.002, hand=0.1) for i in range(20)])
    assert far == pytest.approx(near, rel=0.15)


def test_a_bad_pinch_value_falls_back_to_the_palm():
    from handoff.cv.samples import _tip_weight

    assert _tip_weight(float("nan"), 0.0) == 0.0
    assert _tip_weight(0.9, float("inf")) == 0.0
    assert _tip_weight(0.9, 0.0) == 1.0 and _tip_weight(config.CV_PINCH_ON, 0.0) == 0.0

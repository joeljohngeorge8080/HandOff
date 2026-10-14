import math

import pytest

from handoff import config
from handoff.cv.relative import RelativePointer

SCREEN = (1920, 1080)
CAM = (640, 480)


def make(start=(960.0, 540.0)):
    pos = {"v": start}
    rp = RelativePointer(SCREEN, CAM, lambda: pos["v"])
    return rp, pos


def sweep(rp, t0, x0, y0, dx_total, dy_total, seconds, fps=60):
    """Move the anchor in a straight line at constant speed; return the last result."""
    n = max(1, round(seconds * fps))
    out = None
    for i in range(1, n + 1):
        f = i / n
        out = rp.update(t0 + i / fps, x0 + dx_total * f, y0 + dy_total * f)
    return out


def start(rp, t=0.0, x=0.5, y=0.5):
    return rp.update(t, x, y)


def test_the_first_sample_does_not_move_the_cursor_and_starts_at_the_os_position():
    rp, _ = make(start=(300.0, 200.0))
    assert start(rp) == (300.0, 200.0, True)


def test_the_cursor_starts_inside_the_screen_even_if_the_os_reports_outside():
    rp, _ = make(start=(-50.0, 99999.0))
    x, y, _ = start(rp)
    assert (x, y) == (0.0, 1079.0)


def test_moving_right_moves_the_cursor_right_and_down_moves_it_down():
    rp, _ = make()
    start(rp)
    x, y, _ = sweep(rp, 0.0, 0.5, 0.5, 0.05, 0.05, 0.5)
    assert x > 960 and y > 540


def test_faster_hand_movement_is_amplified_more_than_slow_movement():
    slow, _ = make()
    fast, _ = make()
    start(slow)
    start(fast)
    sx, _, _ = sweep(slow, 0.0, 0.5, 0.5, 0.02, 0, 2.0)  # 0.01 camera-widths per second
    fx, _, _ = sweep(fast, 0.0, 0.5, 0.5, 0.02, 0, 0.02)  # 1.0 camera-widths per second
    assert (fx - 960) > 2 * (sx - 960) > 0


def test_gain_is_capped_at_the_slow_and_fast_ends():
    rp, _ = make()
    start(rp)
    # well below the slow speed: exactly the slow gain
    x, _, _ = sweep(rp, 0.0, 0.5, 0.5, 0.01, 0, 4.0)
    assert x - 960 == pytest.approx(0.01 * config.CV_GAIN_SLOW * SCREEN[0], rel=0.05)
    # far above the fast speed: never more than the fast gain
    rp2, _ = make()
    start(rp2)
    x2, _, _ = sweep(rp2, 0.0, 0.5, 0.5, 0.05, 0, 0.005, fps=1000)
    assert x2 - 960 <= 0.05 * config.CV_GAIN_FAST * SCREEN[0] + 1e-6


def test_the_same_physical_distance_moves_x_and_y_by_the_same_number_of_pixels():
    a, _ = make()
    b, _ = make()
    start(a)
    start(b)
    cam_w, cam_h = CAM
    ax, _, _ = sweep(a, 0.0, 0.5, 0.5, 0.02, 0, 0.1)
    _, by, _ = sweep(b, 0.0, 0.5, 0.5, 0, 0.02 * cam_w / cam_h, 0.1)  # same camera px
    assert (ax - 960) == pytest.approx(by - 540, rel=0.02)


def test_a_brisk_comfortable_sweep_crosses_the_whole_screen():
    rp, _ = make(start=(0.0, 540.0))
    start(rp, x=0.35)
    x, _, _ = sweep(rp, 0.0, 0.35, 0.5, 1 / 3, 0, 0.33)
    assert x == SCREEN[0] - 1


def test_a_slow_small_movement_stays_precise():
    rp, _ = make()
    start(rp)
    x, _, _ = sweep(rp, 0.0, 0.5, 0.5, 0.03, 0, 1.0)
    assert 0 < x - 960 < 100


@pytest.mark.parametrize("dx,edge", [(-1, 0.0), (1, 1919.0)])
def test_the_cursor_clamps_at_the_screen_edge(dx, edge):
    rp, _ = make()
    start(rp)
    x, _, _ = sweep(rp, 0.0, 0.5, 0.5, dx * 0.4, 0, 0.3)
    assert x == edge


def test_vertical_clamping():
    rp, _ = make()
    start(rp)
    _, y, _ = sweep(rp, 0.0, 0.5, 0.5, 0, 0.4, 0.3)
    assert y == 1079.0
    _, y, _ = sweep(rp, 0.4, 0.5, 0.9, 0, -0.5, 0.3)
    assert y == 0.0


def test_reversing_at_the_edge_moves_the_cursor_immediately_with_no_windup():
    rp, _ = make()
    start(rp)
    x, _, _ = sweep(rp, 0.0, 0.5, 0.5, 0.4, 0, 0.3)  # slam into the right edge, hand keeps going
    assert x == 1919.0
    # hand has moved on to 0.9; coming back just 0.02 must already pull the cursor off the edge
    x2, _, _ = sweep(rp, 0.3, 0.9, 0.5, -0.02, 0, 0.05)
    assert x2 < 1919.0


def test_lifting_the_hand_out_of_view_and_back_elsewhere_causes_no_jump():
    rp, pos = make()
    start(rp)
    x_before, y_before, _ = sweep(rp, 0.0, 0.5, 0.5, 0.05, 0, 0.2)
    pos["v"] = (x_before, y_before)  # the real cursor is where we left it
    # hand gone for a second, comes back at the far side of the frame
    x, y, reacquired = rp.update(1.5, 0.1, 0.9)
    assert reacquired and (x, y) == pytest.approx((x_before, y_before), abs=1.0)
    # and the next motion is relative to the new spot
    x2, y2, again = rp.update(1.5 + 1 / 60, 0.1, 0.9)
    assert not again and (x2, y2) == pytest.approx((x, y))


def test_a_short_dropout_is_not_a_lift():
    rp, _ = make()
    start(rp)
    _, _, reacquired = rp.update(config.CV_REFERENCE_GAP_SECONDS - 0.01, 0.52, 0.5)
    assert not reacquired


def test_a_gap_beyond_the_threshold_is_a_lift():
    rp, _ = make()
    start(rp)
    _, _, reacquired = rp.update(config.CV_REFERENCE_GAP_SECONDS + 0.01, 0.52, 0.5)
    assert reacquired


def test_after_a_lift_the_cursor_is_resynced_from_the_real_mouse():
    rp, pos = make()
    start(rp)
    pos["v"] = (111.0, 222.0)  # the user moved the physical mouse meanwhile
    x, y, reacquired = rp.update(5.0, 0.5, 0.5)
    assert reacquired and (x, y) == (111.0, 222.0)


def test_a_single_frame_landmark_glitch_does_not_throw_the_cursor():
    rp, _ = make()
    start(rp)
    x0, y0, _ = rp.update(1 / 60, 0.5, 0.5)
    x1, y1, _ = rp.update(2 / 60, 0.5 + config.CV_MAX_STEP + 0.1, 0.5)
    assert (x1, y1) == (x0, y0)
    # the reference followed the glitch, so normal tracking resumes without a second jump
    x2, _, _ = rp.update(3 / 60, 0.5 + config.CV_MAX_STEP + 0.1, 0.5)
    assert x2 == pytest.approx(x0)


@pytest.mark.parametrize("t", [0.0, -1.0])
def test_zero_or_backwards_time_changes_nothing(t):
    rp, _ = make()
    start(rp, t=1.0)
    assert rp.update(t, 0.9, 0.9)[:2] == (960.0, 540.0)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf")])
def test_non_finite_input_never_corrupts_the_cursor(bad):
    rp, _ = make()
    start(rp)
    x, y, _ = rp.update(1 / 60, bad, 0.5)
    assert math.isfinite(x) and math.isfinite(y)
    x2, y2, _ = rp.update(2 / 60, 0.5, 0.5)
    assert (x2, y2) == (960.0, 540.0)


def test_the_hand_standing_still_leaves_the_cursor_alone():
    rp, _ = make()
    start(rp)
    for i in range(1, 120):
        x, y, _ = rp.update(i / 60, 0.5, 0.5)
    assert (x, y) == (960.0, 540.0)

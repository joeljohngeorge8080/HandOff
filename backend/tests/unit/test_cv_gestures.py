from handoff import config
from handoff.cv.filters import OneEuro
from handoff.cv.gestures import (
    CancelDrag,
    Down,
    GestureMachine,
    HandSample,
    Move,
    Up,
)
from handoff.cv.mapping import to_screen

CLOSED = config.CV_PINCH_ON - 0.05
OPEN = config.CV_PINCH_OFF + 0.05
IN_BETWEEN = (config.CV_PINCH_ON + config.CV_PINCH_OFF) / 2
RELAXED = 0.6  # the old "click zone": must never press or click anything


def sample(t, x=100.0, y=100.0, pinch=OPEN, reacquired=False):
    return HandSample(t=t, x=x, y=y, pinch=pinch, reacquired=reacquired)


def kinds(actions):
    return [type(a).__name__ for a in actions]


def run(m, frames):
    """frames: list of (t, HandSample|None); returns all actions."""
    out = []
    for t, s in frames:
        out += m.step(t, s)
    return out


def test_closing_the_pinch_presses_once_and_opening_it_releases_once():
    m = GestureMachine()
    out = run(m, [(0.00, sample(0.00, pinch=CLOSED)), (0.01, sample(0.01, pinch=CLOSED))])
    assert kinds(out).count("Down") == 1
    out = run(m, [(0.02, sample(0.02, pinch=OPEN)), (0.03, sample(0.03, pinch=OPEN))])
    assert kinds(out).count("Up") == 1
    assert not m.pressed


def test_a_relaxed_hand_never_presses_or_clicks():
    m = GestureMachine()
    out = run(m, [(i * 0.01, sample(i * 0.01, pinch=RELAXED)) for i in range(50)])
    assert "Down" not in kinds(out) and "Up" not in kinds(out)


def test_no_click_follows_a_drop():
    m = GestureMachine()
    run(m, [(0.0, sample(0.0, pinch=CLOSED))])
    pinches = [OPEN, OPEN, RELAXED, RELAXED]
    out = run(m, [(0.01 * i, sample(0.01 * i, pinch=p)) for i, p in enumerate(pinches, 1)])
    assert kinds(out).count("Up") == 1 and kinds(out).count("Down") == 0


def test_hysteresis_keeps_the_button_down_between_the_thresholds():
    m = GestureMachine()
    run(m, [(0.0, sample(0.0, pinch=CLOSED))])
    out = run(m, [(0.01 * i, sample(0.01 * i, pinch=IN_BETWEEN)) for i in range(1, 20)])
    assert m.pressed and "Up" not in kinds(out)


def test_one_noisy_open_frame_does_not_release():
    m = GestureMachine()
    run(m, [(0.0, sample(0.0, pinch=CLOSED))])
    run(m, [(0.01, sample(0.01, pinch=OPEN))])
    run(m, [(0.02, sample(0.02, pinch=CLOSED))])
    assert m.pressed
    out = run(m, [(0.03, sample(0.03, pinch=OPEN))])
    assert "Up" not in kinds(out)  # the counter was reset by the closed frame


def test_losing_the_hand_mid_drag_cancels_the_drag_before_releasing():
    m = GestureMachine()
    run(m, [(0.0, sample(0.0, x=100, pinch=CLOSED))])
    # drag well past the radius
    for i in range(1, 30):
        t = i * 0.01
        m.step(t, sample(t, x=100 + i * 20, pinch=CLOSED))
    out = m.step(1.0, sample(0.3, x=700, pinch=CLOSED))  # stale sample: hand lost
    assert kinds(out)[0] == "CancelDrag"
    assert not m.pressed


def test_losing_the_hand_during_a_plain_press_just_releases_without_esc():
    m = GestureMachine()
    run(m, [(0.0, sample(0.0, pinch=CLOSED))])
    out = m.step(5.0, None)
    assert kinds(out)[0] == "Up" and not any(isinstance(a, CancelDrag) for a in out)


def test_a_hand_that_is_missing_while_idle_does_nothing():
    m = GestureMachine()
    assert m.step(1.0, None) == []


def test_stale_sample_within_grace_still_counts_as_tracking():
    m = GestureMachine()
    out = m.step(0.2, sample(0.0, pinch=CLOSED))  # 0.2 s old < 0.3 s grace
    assert "Down" in kinds(out)


def test_cursor_glides_toward_the_target_but_snaps_when_reacquired():
    m = GestureMachine()
    m.step(0.0, sample(0.0, x=0, y=0))
    out = m.step(0.005, sample(0.005, x=1000, y=0))
    first = next(a for a in out if isinstance(a, Move))
    assert 0 < first.x < 1000
    out = m.step(0.010, sample(0.010, x=1000, y=0, reacquired=True))
    assert next(a for a in out if isinstance(a, Move)).x == 1000


def test_move_is_emitted_only_when_the_pixel_changes():
    m = GestureMachine()
    m.step(0.0, sample(0.0))
    assert not any(isinstance(a, Move) for a in m.step(0.01, sample(0.01)))


def test_press_happens_at_the_cursor_not_the_raw_target():
    m = GestureMachine()
    out = m.step(0.0, sample(0.0, x=50, y=60, pinch=CLOSED))
    assert isinstance(out[0], Move) and (out[0].x, out[0].y) == (50, 60)
    assert isinstance(out[1], Down)  # move first, then press


def test_release_after_losing_hand_allows_pressing_again():
    m = GestureMachine()
    run(m, [(0.0, sample(0.0, pinch=CLOSED))])
    m.step(5.0, None)
    out = m.step(5.1, sample(5.1, pinch=CLOSED, reacquired=True))
    assert any(isinstance(a, Down) for a in out)


def test_up_is_never_emitted_without_a_prior_down():
    m = GestureMachine()
    out = run(m, [(0.01 * i, sample(0.01 * i, pinch=OPEN)) for i in range(10)])
    assert not any(isinstance(a, Up) for a in out)


def test_mapping_stretches_the_active_window_over_the_screen():
    r = (0.15, 0.85)
    assert to_screen(0.15, 0.15, (1920, 1080), r, r) == (0.0, 0.0)
    assert to_screen(0.85, 0.85, (1920, 1080), r, r) == (1919.0, 1079.0)
    assert to_screen(0.5, 0.5, (1921, 1081), r, r) == (960.0, 540.0)


def test_mapping_clamps_outside_the_window_and_survives_hostile_values():
    r = (0.15, 0.85)
    assert to_screen(-5, 9, (100, 100), r, r) == (0.0, 99.0)


def test_one_euro_first_sample_passes_through_and_old_timestamps_are_ignored():
    f = OneEuro(1.5, 0.012)
    assert f(10.0, 0.0) == 10.0
    assert f(99.0, 0.0) == 10.0  # dt == 0
    assert f(99.0, -1.0) == 10.0  # time went backwards


def test_one_euro_smooths_small_jitter_but_tracks_a_fast_move():
    f = OneEuro(1.5, 0.012)
    f(0.0, 0.0)
    jitter = f(1.0, 0.01)
    assert jitter < 0.5
    g = OneEuro(1.5, 0.012)
    g(0.0, 0.0)
    out = 0.0
    for i in range(1, 60):
        out = g(1000.0, i * 0.01)
    assert out > 900


def test_one_euro_reset_forgets_history():
    f = OneEuro(1.5, 0.012)
    f(0.0, 0.0)
    f(5.0, 0.1)
    f.reset()
    assert f(42.0, 0.2) == 42.0

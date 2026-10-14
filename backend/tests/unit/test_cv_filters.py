"""The pointer's one-euro filter, with the configured parameters (hand-size units, ADR-065)."""

import random
import statistics

from handoff import config
from handoff.cv.filters import OneEuro

FPS = 30
U = 1 / config.CV_HAND_WIDTHS  # hand sizes per camera-width


def configured() -> OneEuro:
    return OneEuro(config.CV_MIN_CUTOFF, config.CV_BETA, config.CV_D_CUTOFF)


def test_a_moving_hand_is_followed_with_little_lag():
    # regression: beta 0.012 was tuned for pixels; on 0..1 coordinates it lagged ~100 ms
    f = configured()
    for i in range(FPS):
        f(0.3, i / FPS)
    speed = 1.0 * U  # 1 camera-width per second: a brisk but ordinary movement
    for i in range(1, FPS // 2 + 1):
        x = 0.3 + speed * i / FPS
        y = f(x, 1 + i / FPS)
    assert (x - y) / speed < 0.03  # under 30 ms behind


def test_a_still_hand_stays_still():
    random.seed(7)
    f = configured()
    noise = 0.002 * U  # typical landmark jitter (0.002 camera-widths)
    out = [f(0.5 + random.gauss(0, noise), i / FPS) for i in range(300)]
    assert statistics.pstdev(out[50:]) < noise / 2

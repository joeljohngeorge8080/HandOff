"""Camera-normalised coordinates -> screen pixels."""

from __future__ import annotations


def clamp01(v: float) -> float:
    return max(0.0, min(1.0, v))


def to_screen(
    nx: float,
    ny: float,
    screen: tuple[int, int],
    x_range: tuple[float, float],
    y_range: tuple[float, float],
) -> tuple[float, float]:
    """Stretch the active camera window over the whole screen; outside it clamps to the edge."""
    w, h = screen
    sx = clamp01((nx - x_range[0]) / (x_range[1] - x_range[0])) * (w - 1)
    sy = clamp01((ny - y_range[0]) / (y_range[1] - y_range[0])) * (h - 1)
    return sx, sy

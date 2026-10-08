"""Canonical computer-vision event names (ADR-052).

Hand control (ADR-056) reports only `gesture_detected` / `direction_detected` to the UI, over the
supervisor's local pipe. `CV_INTEGRATION_ENABLED` stays False: it guards the LAN-facing
`POST /internal/v1/cv/events` endpoint (API §42), which must never exist. CV may never bypass
the application state machine (API §41).
"""

from __future__ import annotations

from enum import StrEnum

CV_INTEGRATION_ENABLED = False


class CvEvent(StrEnum):
    POINTER_MOVE = "pointer_move"
    POINTER_CLICK = "pointer_click"
    POINTER_DOWN = "pointer_down"
    POINTER_UP = "pointer_up"
    SELECTION_CHANGED = "selection_changed"
    DRAG_START = "drag_start"
    DRAG_MOVE = "drag_move"
    DRAG_END = "drag_end"
    GRAB = "grab"
    RELEASE = "release"
    GESTURE_DETECTED = "gesture_detected"
    DIRECTION_DETECTED = "direction_detected"

"""Canonical computer-vision event names (ADR-052). Reserved for Phase 2.

Nothing in Phase 1 produces or consumes these; the integration layer is disabled and the
CV module may never bypass the application state machine (API §41-42).
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

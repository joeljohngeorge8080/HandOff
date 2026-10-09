from handoff.cv.events import CV_INTEGRATION_ENABLED, CvEvent


def test_cv_event_names_match_adr_052():
    assert {e.value for e in CvEvent} == {
        "pointer_move", "pointer_click", "pointer_down", "pointer_up",
        "selection_changed",
        "drag_start", "drag_move", "drag_end",
        "grab", "release",
        "gesture_detected", "direction_detected",
    }  # fmt: skip


def test_the_lan_facing_cv_endpoint_stays_disabled():  # API §42 / ADR-056
    assert CV_INTEGRATION_ENABLED is False

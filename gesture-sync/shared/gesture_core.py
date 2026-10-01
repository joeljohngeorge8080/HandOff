"""
gesture_core.py -- Shared Gesture Detection Logic
Used by both Windows client overlay and file transfer.

Updated for MediaPipe >= 1.0 (Tasks API - mp.solutions removed).
Supports: cursor tracking, pinch-to-click, fist GRAB/DROP, clean dot visualization.
"""

import cv2
import mediapipe as mp
import numpy as np
import time
import os
import math

from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision


class Gesture:
    NONE         = "NONE"
    GRAB         = "GRAB"         # Fist (all fingers curled) - hold 0.4s
    DROP         = "DROP"         # Open hand after confirmed GRAB
    HOVER        = "HOVER"        # Open palm
    PINCH        = "PINCH"        # Thumb + index finger pinch (left click)
    RIGHT_PINCH  = "RIGHT_PINCH"  # Thumb + middle finger pinch (right click)
    SCROLL_UP    = "SCROLL_UP"
    SCROLL_DOWN  = "SCROLL_DOWN"


# Landmark indices for reference
class LM:
    WRIST = 0
    THUMB_TIP = 4
    INDEX_MCP = 5
    INDEX_TIP = 8
    MIDDLE_TIP = 12
    RING_TIP = 16
    PINKY_TIP = 20
    # PIP joints (for curl detection)
    INDEX_PIP = 6
    MIDDLE_PIP = 10
    RING_PIP = 14
    PINKY_PIP = 18


class GestureDetector:
    """
    Detects hand gestures using MediaPipe HandLandmarker (Tasks API).
    Returns gesture events + cursor position for overlay control.
    """

    # Key landmarks to draw as dots (fingertips + key joints)
    DOT_LANDMARKS = [
        0,              # Wrist
        4,              # Thumb tip
        5, 6, 8,        # Index: MCP, PIP, tip
        9, 10, 12,      # Middle: MCP, PIP, tip
        13, 14, 16,     # Ring: MCP, PIP, tip
        17, 18, 20,     # Pinky: MCP, PIP, tip
    ]

    # Color scheme for dots
    DOT_COLORS = {
        "tip":   (0, 255, 200),   # Cyan-green for fingertips
        "joint": (100, 100, 255), # Soft blue for joints
        "wrist": (255, 200, 0),   # Amber for wrist
        "pinch": (0, 0, 255),     # Red for active pinch
        "grab":  (0, 255, 0),     # Green for grab
    }

    FINGERTIP_IDS = {4, 8, 12, 16, 20}

    def __init__(self, max_num_hands=1, detection_confidence=0.7, tracking_confidence=0.6):
        # Resolve model path relative to this file
        model_path = os.path.join(os.path.dirname(__file__), "hand_landmarker.task")
        if not os.path.exists(model_path):
            raise FileNotFoundError(
                f"MediaPipe model not found at {model_path}\n"
                "Download it from: https://storage.googleapis.com/mediapipe-models/"
                "hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task"
            )

        base_options = mp_python.BaseOptions(model_asset_path=model_path)
        options = vision.HandLandmarkerOptions(
            base_options=base_options,
            running_mode=vision.RunningMode.VIDEO,
            num_hands=max_num_hands,
            min_hand_detection_confidence=detection_confidence,
            min_hand_presence_confidence=detection_confidence,
            min_tracking_confidence=tracking_confidence,
        )
        self.landmarker = vision.HandLandmarker.create_from_options(options)
        self._frame_ts_ms = 0

        # State tracking
        self.prev_gesture       = Gesture.NONE
        self.gesture_start_time = time.time()
        self.grab_confirmed     = False

        # Pinch state
        self._pinch_active      = False
        self._right_pinch_active = False
        self._pinch_cooldown    = 0.3   # seconds between pinch events
        self._last_pinch_time   = 0
        self._pinch_frame_count = 0
        self._pinch_frame_threshold = 2

        # Cursor smoothing
        self._cursor_x = 0.5
        self._cursor_y = 0.5
        self._smoothing = 0.35  # Lower = smoother but more laggy

        # Scroll tracking
        self._prev_index_y = None
        self._scroll_threshold = 0.03

        # Cooldown between GRAB/DROP events
        self._last_event_time = 0
        self._cooldown        = 0.6

        # Pinch distance threshold (normalized coordinates)
        self._pinch_threshold = 0.055

    # ------------------------------------------------------------------ #
    #  Public API                                                          #
    # ------------------------------------------------------------------ #

    def process_frame(self, frame, draw=True):
        """
        Process a BGR camera frame.
        Returns: (gesture_event, annotated_frame, cursor_pos, landmarks_normalized)
          gesture_event  -- one of Gesture.* or None
          annotated_frame -- frame with dots drawn (if draw=True)
          cursor_pos     -- (x, y) normalized 0..1 for cursor mapping
          landmarks_norm -- list of (x, y, z) for all 21 landmarks, or None
        """
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)

        self._frame_ts_ms += 33  # ~30 fps
        results = self.landmarker.detect_for_video(mp_image, self._frame_ts_ms)

        gesture_event = None
        cursor_pos = (self._cursor_x, self._cursor_y)
        landmarks_norm = None
        current_gesture = Gesture.NONE
        confidence = 0.0

        if results.hand_landmarks:
            landmarks = results.hand_landmarks[0]
            landmarks_norm = [(lm.x, lm.y, lm.z) for lm in landmarks]

            # Update cursor position (index finger tip)
            raw_x = landmarks[LM.INDEX_TIP].x
            raw_y = landmarks[LM.INDEX_TIP].y

            # Smooth the cursor
            self._cursor_x += (raw_x - self._cursor_x) * self._smoothing
            self._cursor_y += (raw_y - self._cursor_y) * self._smoothing
            cursor_pos = (self._cursor_x, self._cursor_y)

            # Classify gesture
            current_gesture = self._classify(landmarks, frame.shape)

            # Check for pinch events first (higher priority for cursor mode)
            pinch_event = self._check_pinch(landmarks)
            if pinch_event:
                gesture_event = pinch_event
            else:
                # GRAB/DROP state machine
                gesture_event = self._state_machine(current_gesture)

            self.prev_gesture = current_gesture

            # Draw clean dots
            if draw:
                self._draw_dots(frame, landmarks, current_gesture)
        else:
            self.prev_gesture = Gesture.NONE
            self.grab_confirmed = False
            self._pinch_active = False
            self._right_pinch_active = False
            self._prev_index_y = None

        # Extract confidence if available
        if results.handedness:
            confidence = results.handedness[0][0].score

        # Draw event label
        if draw and gesture_event:
            self._draw_event(frame, gesture_event)

        return gesture_event, frame, cursor_pos, landmarks_norm, current_gesture, confidence

    # ------------------------------------------------------------------ #
    #  Gesture classification                                              #
    # ------------------------------------------------------------------ #

    def _classify(self, landmarks, shape) -> str:
        lm = landmarks

        # Finger curl detection
        index_curled  = lm[LM.INDEX_TIP].y  > lm[LM.INDEX_PIP].y
        middle_curled = lm[LM.MIDDLE_TIP].y > lm[LM.MIDDLE_PIP].y
        ring_curled   = lm[LM.RING_TIP].y   > lm[LM.RING_PIP].y
        pinky_curled  = lm[LM.PINKY_TIP].y  > lm[LM.PINKY_PIP].y

        all_curled = index_curled and middle_curled and ring_curled and pinky_curled

        if all_curled:
            return Gesture.GRAB
            
        if not index_curled and not middle_curled and ring_curled and pinky_curled:
            return "SCROLL"

        pinky_only = index_curled and middle_curled and ring_curled and not pinky_curled
        
        if pinky_only:
            return Gesture.DROP

        return Gesture.HOVER

    def _check_pinch(self, landmarks) -> str:
        """Detect pinch gestures (thumb + index or thumb + middle) with hysteresis."""
        thumb = landmarks[LM.THUMB_TIP]
        index = landmarks[LM.INDEX_TIP]
        middle = landmarks[LM.MIDDLE_TIP]

        # Distance calculations
        thumb_index_dist = math.sqrt(
            (thumb.x - index.x)**2 + (thumb.y - index.y)**2
        )
        thumb_middle_dist = math.sqrt(
            (thumb.x - middle.x)**2 + (thumb.y - middle.y)**2
        )

        # Hysteresis threshold (harder to release than to start)
        # Reduced base threshold so user has to press fingers together to click
        pinch_threshold = 0.04
        release_threshold = pinch_threshold * 1.5

        # Left click / Drag: thumb + index pinch
        if self._pinch_active:
            if thumb_index_dist > release_threshold:
                self._pinch_active = False
                self._pinch_frame_count = 0
                return "PINCH_END"
            else:
                return "PINCH_HOLD"
        else:
            if thumb_index_dist < pinch_threshold:
                self._pinch_frame_count += 1
                if self._pinch_frame_count >= self._pinch_frame_threshold:
                    self._pinch_active = True
                    return "PINCH_START"
            else:
                self._pinch_frame_count = 0

        # Right click: thumb + middle pinch
        now = time.time()
        if self._right_pinch_active:
            if thumb_middle_dist > release_threshold:
                self._right_pinch_active = False
        else:
            if thumb_middle_dist < pinch_threshold:
                if now - self._last_pinch_time > self._pinch_cooldown:
                    self._right_pinch_active = True
                    self._last_pinch_time = now
                    return Gesture.RIGHT_PINCH

        return None

    def _state_machine(self, current: str):
        """GRAB/DROP state machine."""
        now = time.time()

        if current == Gesture.GRAB:
            if now - self._last_event_time > self._cooldown:
                self._last_event_time = now
                return Gesture.GRAB
                
        elif current == Gesture.DROP:
            if now - self._last_event_time > self._cooldown:
                self._last_event_time = now
                return Gesture.DROP

        return None

    # ------------------------------------------------------------------ #
    #  Clean dot visualization                                             #
    # ------------------------------------------------------------------ #

    def _draw_dots(self, frame, landmarks, current_gesture):
        """Draw minimal, clean dots at key hand landmarks."""
        h, w, _ = frame.shape

        is_grabbing = current_gesture == Gesture.GRAB

        # Check pinch state for coloring
        thumb = landmarks[LM.THUMB_TIP]
        index = landmarks[LM.INDEX_TIP]
        thumb_index_dist = math.sqrt(
            (thumb.x - index.x)**2 + (thumb.y - index.y)**2
        )
        is_pinching = thumb_index_dist < self._pinch_threshold

        for i in self.DOT_LANDMARKS:
            if i >= len(landmarks):
                continue

            lm = landmarks[i]
            px = int(lm.x * w)
            py = int(lm.y * h)

            # Choose color based on state
            if is_grabbing:
                color = self.DOT_COLORS["grab"]
            elif is_pinching and i in (LM.THUMB_TIP, LM.INDEX_TIP):
                color = self.DOT_COLORS["pinch"]
            elif i == LM.WRIST:
                color = self.DOT_COLORS["wrist"]
            elif i in self.FINGERTIP_IDS:
                color = self.DOT_COLORS["tip"]
            else:
                color = self.DOT_COLORS["joint"]

            # Fingertips get larger dots
            radius = 7 if i in self.FINGERTIP_IDS else 4

            # Draw dot with subtle glow
            cv2.circle(frame, (px, py), radius + 3, color, 1)  # outer ring
            cv2.circle(frame, (px, py), radius, color, -1)     # filled center

        # Draw cursor indicator on index finger tip
        ix = int(landmarks[LM.INDEX_TIP].x * w)
        iy = int(landmarks[LM.INDEX_TIP].y * h)
        cv2.circle(frame, (ix, iy), 12, (255, 255, 255), 1)  # cursor ring

        # Draw pinch line when close
        if is_pinching:
            tx = int(thumb.x * w)
            ty = int(thumb.y * h)
            cv2.line(frame, (tx, ty), (ix, iy), self.DOT_COLORS["pinch"], 2)

    def _draw_event(self, frame, event):
        color_map = {
            Gesture.GRAB:        (0, 255, 0),
            Gesture.DROP:        (0, 100, 255),
            Gesture.PINCH:       (0, 200, 255),
            Gesture.RIGHT_PINCH: (255, 100, 0),
            Gesture.HOVER:       (255, 200, 0),
        }
        color = color_map.get(event, (255, 255, 255))
        cv2.putText(frame, f"{event}", (20, 50),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.2, color, 2)

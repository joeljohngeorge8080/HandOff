"""Camera + MediaPipe hand landmarker. Heavy imports are lazy so the core never loads them."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

from handoff import config
from handoff.cv.samples import Detection, analyze
from handoff.errors import HandOffError

# Set True only after the pinch thresholds were re-tuned against world landmarks.
USE_WORLD_PINCH = False


class HandTracker:
    def __init__(self, model_path: Path) -> None:
        if not model_path.is_file():
            raise HandOffError(
                "CV_UNAVAILABLE",
                f"The hand-tracking model is missing ({model_path.name}). Reinstall HandOff.",
            )
        try:
            import cv2 as cv2_module
            import mediapipe as mp
            from mediapipe.tasks.python import BaseOptions, vision
        except Exception as exc:
            raise HandOffError("CV_UNAVAILABLE", f"Hand tracking is unavailable: {exc}") from exc
        cv2: Any = cv2_module
        self._cv2 = cv2
        self._mp: Any = mp
        w, h = config.CV_CAMERA_SIZE
        backend = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY
        cap = cv2.VideoCapture(config.CV_CAMERA_INDEX, backend)
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
        cap.set(cv2.CAP_PROP_FPS, config.CV_CAMERA_FPS)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        if not cap.isOpened():
            cap.release()
            raise HandOffError("CV_CAMERA_UNAVAILABLE", "Could not open the camera.")
        self._cap = cap
        try:
            self._landmarker: Any = vision.HandLandmarker.create_from_options(
                vision.HandLandmarkerOptions(
                    base_options=BaseOptions(model_asset_path=str(model_path)),
                    running_mode=vision.RunningMode.VIDEO,
                    num_hands=1,
                    min_hand_detection_confidence=0.5,
                    min_hand_presence_confidence=0.5,
                    min_tracking_confidence=0.5,
                )
            )
        except Exception as exc:
            cap.release()
            raise HandOffError("CV_UNAVAILABLE", f"Could not load the hand model: {exc}") from exc
        self._start = time.perf_counter()
        self._last_ts = -1

    def read(self) -> tuple[float, Detection | None] | None:
        """(capture time, detection or None when no hand); None when no frame arrived."""
        ok, frame = self._cap.read()
        if not ok:
            return None
        t = time.perf_counter()
        cv2 = self._cv2
        frame = cv2.flip(frame, 1)  # mirror: moving the hand right moves the pointer right
        h, w = frame.shape[:2]
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        ts = max(int((t - self._start) * 1000), self._last_ts + 1)
        self._last_ts = ts
        result = self._landmarker.detect_for_video(
            self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb), ts
        )
        if not result.hand_landmarks:
            return t, None
        world = result.hand_world_landmarks[0] if result.hand_world_landmarks else None
        return t, analyze(result.hand_landmarks[0], (w, h), world, USE_WORLD_PINCH)

    def close(self) -> None:
        try:
            self._landmarker.close()
        finally:
            self._cap.release()

#!/usr/bin/env python
# -*- coding: utf-8 -*-
import math
import os
import statistics
import sys
import threading
import time
import urllib.request
from collections import deque

import cv2
import mediapipe as mp
import pyautogui
from mediapipe.tasks import python
from mediapipe.tasks.python import vision

# ===================== TUNING KNOBS =====================
CAM_INDEX = 0
CAM_W, CAM_H, CAM_FPS = 640, 480, 60
SHOW_PREVIEW = True

POINTER_MODE = "pinch"

X_MIN, X_MAX = 0.15, 0.85
Y_MIN, Y_MAX = 0.15, 0.85

MIN_CUTOFF = 1.5
BETA = 0.012
D_CUTOFF = 1.0

FOLLOW_TAU = 0.020
CONTROL_HZ = 125

# Gesture Zones (Thumb + Index ratio)[cite: 3]
PINCH_ON = 0.22      # Fingers pressed together -> Drag start[cite: 3]
PINCH_OFF = 0.32     # Fingers released -> Drag end[cite: 3]

CLICK_MIN = 0.45     # Much larger distance lower bound -> Click trigger[cite: 3]
CLICK_MAX = 0.75     # Much larger distance upper bound -> Click trigger[cite: 3]

LOST_GRACE = 0.30
# ==========================================================

pyautogui.PAUSE = 0
pyautogui.FAILSAFE = False
screen_w, screen_h = pyautogui.size()

if sys.platform == "win32":
    import ctypes
    ctypes.windll.winmm.timeBeginPeriod(1)

model_path = "hand_landmarker.task"
if not os.path.exists(model_path):
    print("Downloading hand tracking model (~9MB)... Please wait.")
    urllib.request.urlretrieve(
        "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task",
        model_path,
    )
    print("Download complete!")

options = vision.HandLandmarkerOptions(
    base_options=python.BaseOptions(model_asset_path=model_path),
    running_mode=vision.RunningMode.VIDEO,
    num_hands=1,
    min_hand_detection_confidence=0.5,
    min_hand_presence_confidence=0.5,
    min_tracking_confidence=0.5,
)


class OneEuro:
    def __init__(self, min_cutoff, beta, d_cutoff=1.0):
        self.min_cutoff, self.beta, self.d_cutoff = min_cutoff, beta, d_cutoff
        self.reset()

    def reset(self):
        self.x, self.dx, self.t = None, 0.0, None

    @staticmethod
    def _alpha(cutoff, dt):
        tau = 1.0 / (2 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def __call__(self, x, t):
        if self.x is None:
            self.x, self.t = x, t
            return x
        dt = t - self.t
        if dt <= 0:
            return self.x
        self.t = t
        self.dx += self._alpha(self.d_cutoff, dt) * ((x - self.x) / dt - self.dx)
        cutoff = self.min_cutoff + self.beta * abs(self.dx)
        self.x += self._alpha(cutoff, dt) * (x - self.x)
        return self.x


class Shared:
    def __init__(self):
        self.lock = threading.Lock()
        self.x, self.y = screen_w / 2, screen_h / 2
        self.pinch = 1.0
        self.landmarks = []
        self.last_seen = -1e9
        self.snap = False
        self.running = True
        self.mode = ""


state = Shared()


def open_camera():
    backend = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY
    cap = cv2.VideoCapture(CAM_INDEX, backend)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAM_W)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAM_H)
    cap.set(cv2.CAP_PROP_FPS, CAM_FPS)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    return cap


def clamp01(v):
    return max(0.0, min(1.0, v))


HAND_CONNECTIONS = [
    (0, 1), (1, 2), (2, 3), (3, 4),
    (0, 5), (5, 6), (6, 7), (7, 8),
    (5, 9), (9, 10), (10, 11), (11, 12),
    (9, 13), (13, 14), (14, 15), (15, 16),
    (13, 17), (17, 18), (18, 19), (19, 20),
    (0, 17)
]


def tracking_worker():
    fx = OneEuro(MIN_CUTOFF, BETA, D_CUTOFF)
    fy = OneEuro(MIN_CUTOFF, BETA, D_CUTOFF)
    cap = open_camera()
    if not cap.isOpened():
        print("Could not open camera.")
        state.running = False
        return

    start = time.perf_counter()
    last_ts = -1
    last_seen_local = -1e9

    with vision.HandLandmarker.create_from_options(options) as landmarker:
        while state.running:
            ok, frame = cap.read()
            if not ok:
                time.sleep(0.005)
                continue
            t = time.perf_counter()

            frame = cv2.flip(frame, 1)
            h, w = frame.shape[:2]
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            ts = max(int((t - start) * 1000), last_ts + 1)
            last_ts = ts
            result = landmarker.detect_for_video(
                mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb), ts
            )

            landmarks_px = []
            pinch = 1.0
            if result.hand_landmarks:
                lm = result.hand_landmarks[0]
                landmarks_px = [[int(pt.x * w), int(pt.y * h)] for pt in lm]

                def dist(a, b):
                    return math.hypot((lm[a].x - lm[b].x) * w, (lm[a].y - lm[b].y) * h)

                hand_size = max(dist(0, 9), 1e-6)
                pinch = dist(4, 8) / hand_size

                if POINTER_MODE == "pinch":
                    ax = (lm[4].x + lm[8].x) / 2
                    ay = (lm[4].y + lm[8].y) / 2
                else:
                    ax, ay = lm[8].x, lm[8].y

                sx = clamp01((ax - X_MIN) / (X_MAX - X_MIN)) * (screen_w - 1)
                sy = clamp01((ay - Y_MIN) / (Y_MAX - Y_MIN)) * (screen_h - 1)

                reacquired = (t - last_seen_local) > LOST_GRACE
                if reacquired:
                    fx.reset()
                    fy.reset()
                sx, sy = fx(sx, t), fy(sy, t)
                last_seen_local = t

                with state.lock:
                    state.x, state.y = sx, sy
                    state.pinch = pinch
                    state.landmarks = landmarks_px
                    state.last_seen = t
                    if reacquired:
                        state.snap = True

            current_mode = state.mode
            if SHOW_PREVIEW:
                if landmarks_px:
                    is_dragging = pinch < PINCH_ON
                    is_clicking = CLICK_MIN <= pinch <= CLICK_MAX
                    
                    line_color = (0, 165, 255) if is_dragging else (0, 255, 0) if is_clicking else (200, 200, 200)
                    
                    for conn in HAND_CONNECTIONS:
                        pt1 = tuple(landmarks_px[conn[0]])
                        pt2 = tuple(landmarks_px[conn[1]])
                        is_thumb_index = conn[0] in range(0, 9) and conn[1] in range(0, 9)
                        c = line_color if is_thumb_index else (200, 200, 200)
                        cv2.line(frame, pt1, pt2, c, 2)

                    for idx, pt in enumerate(landmarks_px):
                        c = (0, 165, 255) if (is_dragging and idx in [4, 8]) else (0, 255, 0) if (is_clicking and idx in [4, 8]) else (255, 100, 0)
                        cv2.circle(frame, pt, 5 if idx in [4, 8] else 3, c, cv2.FILLED)

                cv2.putText(frame, f"pinch: {pinch:.2f}  mode: {current_mode or 'MOVE'}", (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
                cv2.imshow("Hand Mouse (q to quit)", frame)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    state.running = False

    cap.release()
    cv2.destroyAllWindows()
    state.running = False


def control_loop():
    cx, cy = screen_w / 2, screen_h / 2
    dragging = False
    clicked_in_zone = False
    last_sent = (-1, -1)
    period = 1.0 / CONTROL_HZ
    prev = time.perf_counter()

    drag_up_count = 0
    REQUIRED_DRAG_UP_FRAMES = 2

    while state.running:
        now = time.perf_counter()
        dt = max(now - prev, 1e-4)
        prev = now

        with state.lock:
            tx, ty = state.x, state.y
            pinch = state.pinch
            seen = state.last_seen
            snap = state.snap
            state.snap = False

        if now - seen < LOST_GRACE:
            if snap:
                cx, cy = tx, ty
            else:
                a = 1.0 - math.exp(-dt / FOLLOW_TAU)
                cx += (tx - cx) * a
                cy += (ty - cy) * a

            in_contact = pinch < PINCH_ON
            in_click_zone = CLICK_MIN <= pinch <= CLICK_MAX

            # 1. Drag Logic (Fingers pressed together continuously with debounce)[cite: 3]
            if not dragging and not clicked_in_zone:
                if in_contact:
                    dragging = True
                    drag_up_count = 0
                    pyautogui.mouseDown()
            elif dragging:
                if pinch > PINCH_OFF:
                    drag_up_count += 1
                    if drag_up_count >= REQUIRED_DRAG_UP_FRAMES:
                        pyautogui.mouseUp()
                        dragging = False
                else:
                    drag_up_count = 0

            # 2. Click Logic (Fingers at a much larger distance)[cite: 3]
            if not clicked_in_zone:
                if in_click_zone and not dragging:
                    pyautogui.click()
                    clicked_in_zone = True
            else:
                if not in_click_zone:
                    clicked_in_zone = False

            with state.lock:
                state.mode = "DRAG" if dragging else "CLICK" if clicked_in_zone else ""

            pos = (int(round(cx)), int(round(cy)))
            if pos != last_sent:
                pyautogui.moveTo(*pos)
                last_sent = pos
        else:
            if dragging:
                pyautogui.mouseUp()
            dragging = False
            clicked_in_zone = False
            drag_up_count = 0
            with state.lock:
                state.mode = ""

        remaining = period - (time.perf_counter() - now)
        if remaining > 0:
            time.sleep(remaining)

    if dragging:
        pyautogui.mouseUp()


if __name__ == "__main__":
    t = threading.Thread(target=tracking_worker, daemon=True)
    t.start()
    print("Hand mouse running. Press 'q' to exit.")
    try:
        control_loop()
    except KeyboardInterrupt:
        pass
    finally:
        state.running = False
        t.join(timeout=2)
        if sys.platform == "win32":
            ctypes.windll.winmm.timeBeginPeriod(1)
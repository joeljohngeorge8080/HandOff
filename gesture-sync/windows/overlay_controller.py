"""
overlay_controller.py -- Transparent Hand-Tracking Overlay for Windows
Creates a fullscreen, click-through, always-on-top transparent window
that shows hand landmarks and controls the mouse cursor.

Features:
  - Index finger tip controls mouse cursor
  - Pinch (thumb + index) = left click
  - Pinch (thumb + middle) = right click
  - Fist (hold 0.4s) = GRAB (capture clipboard/file)
  - Open hand after GRAB = DROP (receive content)

Requires: pyautogui, cv2, mediapipe, pywin32
"""

import tkinter as tk
import ctypes
import time
import threading
import logging
import math
import cv2

# Windows API imports
import win32gui
import win32con
import win32api

log = logging.getLogger("GestureSync-Overlay")


# ------------------------------------------------------------------ #
#  Cursor Controller                                                   #
# ------------------------------------------------------------------ #

class CursorController:
    """Maps hand position to screen cursor with smoothing and acceleration."""

    def _get_cursor_pos(self):
        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
        pt = POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
        return pt.x, pt.y

    def __init__(self):
        # Get screen dimensions
        self.screen_w = win32api.GetSystemMetrics(0)
        self.screen_h = win32api.GetSystemMetrics(1)

        # Sensitivity for trackpad mode
        # Adjust these multipliers to make the mouse move faster/slower
        self.sensitivity_x = self.screen_w * 1.5
        self.sensitivity_y = self.screen_h * 1.8

        # State
        self._prev_x = None
        self._prev_y = None
        # Start at current Windows cursor position
        self._target_x, self._target_y = self._get_cursor_pos()
        self._cursor_x, self._cursor_y = self._target_x, self._target_y
        
        self._dynamic_smooth = 0.5

        # Click state
        self._click_held = False
        self._right_click_held = False

        # Start a dedicated 120Hz update thread for fluid cursor rendering
        self._running = True
        self._thread = threading.Thread(target=self._update_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def _update_loop(self):
        """Runs at ~120Hz to provide buttery smooth cursor interpolation."""
        while self._running:
            # Interpolate current cursor pos towards the target pos
            # We scale the dynamic_smooth down slightly because we're running 4x faster than 30fps
            smooth = self._dynamic_smooth * 0.4
            
            # Only update if we are off by at least a tiny fraction to save CPU
            if abs(self._target_x - self._cursor_x) > 0.1 or abs(self._target_y - self._cursor_y) > 0.1:
                self._cursor_x += (self._target_x - self._cursor_x) * smooth
                self._cursor_y += (self._target_y - self._cursor_y) * smooth
                
                ctypes.windll.user32.SetCursorPos(
                    int(self._cursor_x), int(self._cursor_y)
                )
            
            # 120Hz = 8.3ms per frame
            time.sleep(0.008)

    def reset(self):
        """Reset previous position when hand is lost so it doesn't jump."""
        self._prev_x = None
        self._prev_y = None

    def move(self, norm_x: float, norm_y: float):
        """Update target position based on relative hand movement (trackpad style)."""
        if self._prev_x is None:
            self._prev_x = norm_x
            self._prev_y = norm_y
            # Sync our internal coordinates with actual Windows mouse position
            self._target_x, self._target_y = self._get_cursor_pos()
            self._cursor_x, self._cursor_y = self._target_x, self._target_y
            return
            
        dx = norm_x - self._prev_x
        dy = norm_y - self._prev_y
        
        self._prev_x = norm_x
        self._prev_y = norm_y
        
        # Calculate velocity (distance moved this frame)
        velocity = math.sqrt(dx**2 + dy**2)
        
        # Pointer Acceleration: Trackpads accelerate based on speed.
        # Moving slowly = 1.0x (precise). Moving fast = up to 3.5x (reaches across screen).
        accel = 1.0 + (velocity * 40.0)
        accel = min(accel, 3.5)
        
        # Calculate target position using sensitivity * acceleration
        target_x = self._target_x + (dx * self.sensitivity_x * accel)
        target_y = self._target_y + (dy * self.sensitivity_y * accel)
        
        # Clamp to screen bounds
        self._target_x = max(0, min(self.screen_w - 1, target_x))
        self._target_y = max(0, min(self.screen_h - 1, target_y))
        
        # Dynamic smoothing: Heavy smoothing at low speed (stops jitter), low smoothing at high speed (snappy)
        dynamic_smooth = 0.25 + (velocity * 12.0)
        self._dynamic_smooth = max(0.2, min(0.85, dynamic_smooth))

    def left_click(self):
        """Simulate left mouse click."""
        if not self._click_held:
            log.info("LEFT CLICK (PINCH START) -- Simulating MOUSEEVENTF_LEFTDOWN")
            self._click_held = True
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)

    def left_release(self):
        """Release left mouse button."""
        if self._click_held:
            log.info("LEFT RELEASE (PINCH END) -- Simulating MOUSEEVENTF_LEFTUP")
            self._click_held = False
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
            
    def scroll_mode(self, norm_x: float, norm_y: float):
        """Simulate scroll wheel based on hand movement delta."""
        if self._prev_x is None:
            self._prev_x = norm_x
            self._prev_y = norm_y
            return
            
        dy = norm_y - self._prev_y
        self._prev_x = norm_x
        self._prev_y = norm_y
        
        # Standard scroll tick is 120. Negative dy (moving hand up) = scroll up (positive ticks).
        # We need a big multiplier since dy is small (e.g. 0.01)
        ticks = int(-dy * 5000)
        if abs(ticks) > 10:
            win32api.mouse_event(win32con.MOUSEEVENTF_WHEEL, 0, 0, ticks, 0)

    def right_click(self):
        """Simulate right mouse click (press and release)."""
        win32api.mouse_event(win32con.MOUSEEVENTF_RIGHTDOWN, 0, 0, 0, 0)
        win32api.mouse_event(win32con.MOUSEEVENTF_RIGHTUP, 0, 0, 0, 0)


# ------------------------------------------------------------------ #
#  Transparent Overlay Window                                          #
# ------------------------------------------------------------------ #

class OverlayWindow:
    """
    Fullscreen transparent overlay that shows hand landmark dots
    on top of the Windows desktop. Click-through so it doesn't
    interfere with normal mouse interaction.
    """

    # Dot appearance
    DOT_RADIUS_TIP   = 3     # Very tiny dots
    DOT_RADIUS_JOINT = 1     # Almost invisible joints
    CURSOR_RING_RADIUS = 0   # No ring, just rely on tip dot

    FINGERTIP_IDS = {4, 8, 12, 16, 20}
    KEY_LANDMARKS = [0, 4, 5, 6, 8, 9, 10, 12, 13, 14, 16, 17, 18, 20]

    # Colors (tkinter hex format)
    COLORS = {
        "tip":    "#00FFC8",   # Cyan-green
        "joint":  "#6464FF",   # Soft blue
        "wrist":  "#FFC800",   # Amber
        "pinch":  "#FF3232",   # Red
        "grab":   "#00FF00",   # Green
        "cursor": "#FFFFFF",   # White
        "status": "#00FF88",   # Status text
    }

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("GestureSync Overlay")

        # Get screen size
        self.screen_w = self.root.winfo_screenwidth()
        self.screen_h = self.root.winfo_screenheight()

        # Fullscreen, borderless, always on top
        self.root.overrideredirect(True)
        self.root.geometry(f"{self.screen_w}x{self.screen_h}+0+0")
        self.root.attributes('-topmost', True)

        # Make black pixels transparent
        self.root.configure(bg='black')
        self.root.attributes('-transparentcolor', 'black')

        # Canvas for drawing
        self.canvas = tk.Canvas(
            self.root,
            width=self.screen_w,
            height=self.screen_h,
            bg='black',
            highlightthickness=0,
        )
        self.canvas.pack()

        # Gesture event text at the top (subtle)
        self._event_text = self.canvas.create_text(
            self.screen_w // 2, 40,
            text="",
            fill="#888888",
            font=("Consolas", 12),
        )

        # Make window click-through
        self._make_click_through()

        # Bind exit key
        self.root.bind('<Control-q>', lambda e: self.close())
        self.root.bind('<Escape>', lambda e: self.close())

        self._running = True
        self._dot_items = []  # Track canvas items for cleanup

    def _make_click_through(self):
        """Make the overlay window click-through using Win32 API."""
        # Need to wait for window to be created
        self.root.update_idletasks()
        hwnd = ctypes.windll.user32.FindWindowW(None, "GestureSync Overlay")
        if hwnd:
            # Add WS_EX_TRANSPARENT and WS_EX_LAYERED to extended style
            ex_style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
            win32gui.SetWindowLong(
                hwnd,
                win32con.GWL_EXSTYLE,
                ex_style | win32con.WS_EX_TRANSPARENT | win32con.WS_EX_LAYERED
            )
            log.info("Overlay window set to click-through")
        else:
            log.warning("Could not find overlay window handle")

    def update_landmarks(self, landmarks_norm, gesture_event=None, is_pinching=False, is_grabbing=False):
        """
        Update the overlay with new landmark positions.
        landmarks_norm: list of (x, y, z) in normalized coords (0..1)
        """
        # Clear previous dots
        for item in self._dot_items:
            self.canvas.delete(item)
        self._dot_items.clear()

        if landmarks_norm is None:
            self.canvas.itemconfig(self._event_text, text="")
            return
            
        # Update event text at the top (subtle)
        if gesture_event:
            # Simplify PINCH_START and PINCH_HOLD to just PINCH for display
            display_text = gesture_event.split("_")[0] if "PINCH" in gesture_event else gesture_event
            
            self.canvas.itemconfig(
                self._event_text,
                text=display_text,
                fill="#888888",
            )
        else:
            self.canvas.itemconfig(self._event_text, text="")

        # Cursor ring on index fingertip (Disabled for minimalist look)
        # if len(landmarks_norm) > 8 and self.CURSOR_RING_RADIUS > 0:
        #     ix = int(landmarks_norm[8][0] * self.screen_w)
        #     iy = int(landmarks_norm[8][1] * self.screen_h)
        #     r = self.CURSOR_RING_RADIUS
        #     ring = self.canvas.create_oval(
        #         ix - r, iy - r, ix + r, iy + r,
        #         outline=self.COLORS["cursor"], width=2,
        #     )
        #     self._dot_items.append(ring)

        # Pinch line (Disabled for minimalist look)
        # if is_pinching and len(landmarks_norm) > 8:
        #     tx = int(landmarks_norm[4][0] * self.screen_w)
        #     ty = int(landmarks_norm[4][1] * self.screen_h)
        #     ix = int(landmarks_norm[8][0] * self.screen_w)
        #     iy = int(landmarks_norm[8][1] * self.screen_h)
        #     line = self.canvas.create_line(
        #         tx, ty, ix, iy,
        #         fill=self.COLORS["pinch"], width=2,
        #     )
        #     self._dot_items.append(line)

    def set_status(self, text: str):
        """Update the status text. (Disabled for clean look)"""
        pass

    def close(self):
        """Close the overlay."""
        self._running = False
        self.root.destroy()

    @property
    def running(self):
        return self._running

    def tick(self):
        """Process tkinter events. Call from the main loop."""
        try:
            self.root.update()
        except tk.TclError:
            self._running = False

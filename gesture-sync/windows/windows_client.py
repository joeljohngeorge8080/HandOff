"""
windows_client.py -- GestureSync Windows Client
Two modes:
  1. Normal mode:  OpenCV camera window + file transfer
  2. Overlay mode: Transparent overlay on desktop, hand controls cursor

Requirements:
    pip install opencv-python mediapipe websockets pywin32 pystray pillow requests

Run:
    Normal mode:   python windows_client.py --server ws://localhost:8765/ws/windows
    Overlay mode:  python windows_client.py --overlay --server ws://localhost:8765/ws/windows
"""

import sys
import os
import cv2
import asyncio
import websockets
import json
import argparse
import threading
import time
import logging
import requests
from urllib.parse import urlparse
from pathlib import Path

# Windows clipboard
import win32clipboard
import win32con

# System tray
import pystray
from PIL import Image, ImageDraw

# Add shared module path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'shared'))
from gesture_core import GestureDetector, Gesture

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger("GestureSync-Windows")

# Default download folder for received files
DOWNLOAD_DIR = os.path.join(os.path.expanduser("~"), "Downloads", "GestureSync")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)


# ------------------------------------------------------------------ #
#  Clipboard helpers                                                   #
# ------------------------------------------------------------------ #

CF_HDROP = 15

def get_clipboard() -> dict:
    """Read current Windows clipboard content (text or file paths)."""
    win32clipboard.OpenClipboard()
    try:
        if win32clipboard.IsClipboardFormatAvailable(CF_HDROP):
            file_paths = win32clipboard.GetClipboardData(CF_HDROP)
            if file_paths:
                paths = list(file_paths)
                if len(paths) == 1 and os.path.isfile(paths[0]):
                    return {
                        "type": "file",
                        "filepath": paths[0],
                        "filename": os.path.basename(paths[0]),
                        "size": os.path.getsize(paths[0]),
                    }
                return {
                    "type": "files",
                    "filepaths": [p for p in paths if os.path.isfile(p)],
                }

        if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
            text = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
            return {"type": "text", "content": text}

        if win32clipboard.IsClipboardFormatAvailable(win32con.CF_DIB):
            return {"type": "image_notice", "content": "[Image in clipboard]"}

        return {"type": "empty", "content": ""}
    finally:
        win32clipboard.CloseClipboard()


def set_clipboard(text: str):
    """Write text to Windows clipboard."""
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
    finally:
        win32clipboard.CloseClipboard()


# ------------------------------------------------------------------ #
#  Tray icon                                                           #
# ------------------------------------------------------------------ #

def make_tray_icon(status="idle"):
    img  = Image.new("RGB", (64, 64), color=(30, 30, 30))
    draw = ImageDraw.Draw(img)
    color = {
        "idle":       (100, 100, 100),
        "connected":  (0,   200, 0),
        "grabbed":    (255, 165, 0),
        "receiving":  (0,   100, 255),
        "error":      (200, 0,   0),
        "overlay":    (0,   200, 255),
    }.get(status, (100, 100, 100))
    draw.ellipse([12, 12, 52, 52], fill=color)
    return img


class TrayApp:
    def __init__(self, client):
        self.client = client
        mode_label = "Overlay Mode" if client.overlay_mode else "Normal Mode"
        self.icon = pystray.Icon(
            "GestureSync",
            make_tray_icon("idle"),
            f"GestureSync -- {mode_label}",
            menu=pystray.Menu(
                pystray.MenuItem("Show/Hide Camera", self._toggle_camera),
                pystray.MenuItem("Open Downloads", self._open_downloads),
                pystray.MenuItem("Quit", self._quit),
            )
        )

    def set_status(self, status: str, tooltip: str):
        self.icon.icon  = make_tray_icon(status)
        self.icon.title = f"GestureSync -- {tooltip}"

    def _toggle_camera(self, icon, item):
        self.client.show_camera = not self.client.show_camera

    def _open_downloads(self, icon, item):
        os.startfile(DOWNLOAD_DIR)

    def _quit(self, icon, item):
        self.client.running = False
        icon.stop()

    def run(self):
        self.icon.run()


# ------------------------------------------------------------------ #
#  Main client                                                         #
# ------------------------------------------------------------------ #

class WindowsGestureClient:
    def __init__(self, server_url: str, overlay_mode: bool = False):
        self.server_url   = server_url
        self.overlay_mode = overlay_mode
        self.detector     = GestureDetector()
        self.running      = True
        self.show_camera  = not overlay_mode  # Hide camera in overlay mode
        self.tray         = TrayApp(self)
        self.ws           = None
        self.loop         = None

        # Overlay components (lazy import to avoid dependency in normal mode)
        self.overlay  = None
        self.cursor   = None
        
        self._pinch_start_time = 0.0
        self._is_dragging = False

        # Derive HTTP base URL from WebSocket URL
        parsed = urlparse(server_url)
        scheme = "https" if parsed.scheme == "wss" else "http"
        self.http_base = f"{scheme}://{parsed.hostname}:{parsed.port}"

        path_parts = parsed.path.strip("/").split("/")
        self.device_id = path_parts[-1] if len(path_parts) >= 2 else "windows"

    def start(self):
        # Network thread
        self.loop = asyncio.new_event_loop()
        net_thread = threading.Thread(target=self._run_network, daemon=True)
        net_thread.start()

        # Tray thread
        tray_thread = threading.Thread(target=self.tray.run, daemon=True)
        tray_thread.start()

        if self.overlay_mode:
            self._run_overlay()
        else:
            self._run_camera()

    # ---- Network ----
    def _run_network(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._connect_loop())

    async def _connect_loop(self):
        while self.running:
            try:
                log.info(f"Connecting to {self.server_url} ...")
                async with websockets.connect(self.server_url) as ws:
                    self.ws = ws
                    self.tray.set_status("connected", "Connected")
                    log.info("Connected to relay server")
                    await self._listen(ws)
            except Exception as e:
                log.warning(f"Connection lost: {e}. Retrying in 3s...")
                self.ws = None
                self.tray.set_status("error", "Disconnected")
                await asyncio.sleep(3)

    async def _listen(self, ws):
        async for raw in ws:
            data = json.loads(raw)
            event = data.get("event")

            if event == "GRAB_ACK":
                log.info("Server acknowledged GRAB")
                self.tray.set_status("grabbed", "Content Grabbed!")

            elif event == "STATUS":
                devices = data.get("devices", [])
                log.info(f"Devices online: {devices}")

            elif event == "CONTENT_READY":
                src = data.get("source", "?")
                ctype = data.get("type", "?")
                fname = data.get("filename", "")
                log.info(f"Content ready from {src}: {ctype} {fname}")
                log.info(">> Make DROP gesture (open hand) to receive!")
                self.tray.set_status("receiving", f"Ready from {src}")
                if self.overlay:
                    self.overlay.set_status(
                        f"Content ready from {src} -- DROP to receive"
                    )

            elif event == "RECEIVE":
                await self._handle_receive(data)

            elif event == "RECEIVE_EMPTY":
                log.info("Nothing to receive")

            elif event == "PONG":
                pass

    async def _handle_receive(self, data):
        content_type = data.get("type", "text")

        if content_type == "file":
            file_id = data.get("file_id", "")
            filename = data.get("filename", "file")
            download_url = f"{self.http_base}/download/{file_id}"
            log.info(f"Downloading file: {filename} ...")

            def _download():
                try:
                    resp = requests.get(download_url, stream=True)
                    resp.raise_for_status()
                    dest = os.path.join(DOWNLOAD_DIR, filename)
                    if os.path.exists(dest):
                        name, ext = os.path.splitext(filename)
                        dest = os.path.join(DOWNLOAD_DIR,
                                            f"{name}_{int(time.time())}{ext}")
                    with open(dest, "wb") as f:
                        for chunk in resp.iter_content(8192):
                            f.write(chunk)
                    log.info(f"File saved: {dest}")
                    self.tray.set_status("connected", "File Received!")
                    if self.overlay:
                        self.overlay.set_status(f"Received: {filename}")
                except Exception as e:
                    log.error(f"Download failed: {e}")

            threading.Thread(target=_download, daemon=True).start()

        elif content_type == "text":
            text = data.get("content", "")
            set_clipboard(text)
            log.info(f"Text copied to clipboard: {text[:80]}...")
            self.tray.set_status("connected", "Text Received!")

    # ---- Sending ----
    async def _send_grab_text(self, payload: dict):
        if self.ws:
            msg = {
                "event":        "GRAB",
                "content_type": payload["type"],
                "content":      payload["content"],
            }
            await self.ws.send(json.dumps(msg))
            log.info(f"Sent GRAB (text): {payload['content'][:80]}")

    def _send_grab_file(self, filepath: str, filename: str):
        try:
            log.info(f"Uploading file: {filename} ...")
            with open(filepath, "rb") as f:
                resp = requests.post(
                    f"{self.http_base}/upload",
                    files={"file": (filename, f)},
                    data={"device_id": self.device_id},
                )
            resp.raise_for_status()
            result = resp.json()
            log.info(f"Upload complete: file_id={result['file_id']}")

            if self.ws and self.loop:
                asyncio.run_coroutine_threadsafe(
                    self.ws.send(json.dumps({
                        "event":        "GRAB",
                        "content_type": "file",
                        "file_id":      result["file_id"],
                        "filename":     result["filename"],
                        "size":         result["size"],
                    })),
                    self.loop,
                )
            self.tray.set_status("grabbed", f"Grabbed: {filename}")
            if self.overlay:
                self.overlay.set_status(f"Grabbed: {filename}")
        except Exception as e:
            log.error(f"File upload failed: {e}")

    async def _send_drop(self):
        if self.ws:
            await self.ws.send(json.dumps({"event": "DROP"}))
            log.info("Sent DROP request")

    # ---- Handle gesture events ----
    def _handle_gesture_event(self, event):
        """Process a gesture event (shared by both camera and overlay modes)."""
        import win32api
        import win32con
        
        if event == Gesture.GRAB:
            log.info("GRAB -- simulating Ctrl+C and reading clipboard...")
            if self.overlay_mode:
                # Simulate OS Copy (Ctrl+C) natively
                win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
                win32api.keybd_event(ord('C'), 0, 0, 0)
                win32api.keybd_event(ord('C'), 0, win32con.KEYEVENTF_KEYUP, 0)
                win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)
                time.sleep(0.2) # Give Windows a moment to populate the clipboard
                
            clipboard = get_clipboard()

            if clipboard["type"] == "file":
                threading.Thread(
                    target=self._send_grab_file,
                    args=(clipboard["filepath"], clipboard["filename"]),
                    daemon=True,
                ).start()
            elif clipboard["type"] == "files":
                for fp in clipboard["filepaths"]:
                    threading.Thread(
                        target=self._send_grab_file,
                        args=(fp, os.path.basename(fp)),
                        daemon=True,
                    ).start()
            elif clipboard["type"] in ("text", "image_notice"):
                if clipboard.get("content"):
                    asyncio.run_coroutine_threadsafe(
                        self._send_grab_text(clipboard), self.loop
                    )
            elif clipboard["type"] == "empty":
                log.info("Clipboard is empty")

        elif event == Gesture.DROP:
            log.info("DROP -- requesting content and simulating Ctrl+V...")
            if self.overlay_mode:
                # We request content from server, but if we just want to paste local content
                # immediately, we can do it now. The OS clipboard already has the content!
                win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
                win32api.keybd_event(ord('V'), 0, 0, 0)
                win32api.keybd_event(ord('V'), 0, win32con.KEYEVENTF_KEYUP, 0)
                win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)
                
            if self.loop:
                asyncio.run_coroutine_threadsafe(
                    self._send_drop(), self.loop
                )

        elif event == "PINCH_START":
            if self.overlay_mode and self.cursor:
                self._pinch_start_time = time.time()
                self._is_dragging = False

        elif event == "PINCH_HOLD":
            if self.overlay_mode and self.cursor:
                if not self._is_dragging and (time.time() - self._pinch_start_time > 0.6):
                    log.info("Drag threshold reached, simulating mouse down")
                    self._is_dragging = True
                    self.cursor.left_click()  # Triggers LEFTDOWN

        elif event == "PINCH_END":
            if self.overlay_mode and self.cursor:
                if self._is_dragging:
                    self.cursor.left_release()
                    self._is_dragging = False
                else:
                    # Quick click
                    self.cursor.left_click()
                    self.cursor.left_release()

        elif event == Gesture.RIGHT_PINCH:
            if self.overlay_mode and self.cursor:
                log.info("RIGHT PINCH -- right click")
                self.cursor.right_click()

        elif event is not None:
            log.info(f"Gesture: {event}")

    # ---- Overlay mode ----
    def _run_overlay(self):
        """Run with transparent overlay on desktop."""
        from overlay_controller import OverlayWindow, CursorController

        log.info("Starting overlay mode...")
        self.overlay = OverlayWindow()
        self.cursor  = CursorController()
        self.tray.set_status("overlay", "Overlay Active")

        # Shared data between camera thread and main thread
        self._overlay_data = {
            "landmarks": None,
            "event": None,
            "is_pinching": False,
            "is_grabbing": False,
            "dirty": False,  # True when camera thread has new data
        }
        self._data_lock = threading.Lock()

        # Camera runs in background thread
        cam_thread = threading.Thread(target=self._camera_thread, daemon=True)
        cam_thread.start()

        # Main thread runs tkinter event loop + draws overlay
        log.info("Overlay active! Ctrl+Q or Esc to exit.")
        while self.running and self.overlay.running:
            # Read latest data from camera thread
            with self._data_lock:
                if self._overlay_data["dirty"]:
                    self.overlay.update_landmarks(
                        self._overlay_data["landmarks"],
                        gesture_event=self._overlay_data["event"],
                        is_pinching=self._overlay_data["is_pinching"],
                        is_grabbing=self._overlay_data["is_grabbing"],
                    )
                    self._overlay_data["dirty"] = False

            self.overlay.tick()
            time.sleep(0.016)  # ~60 fps UI refresh

        self.running = False
        log.info("Overlay closed.")

    def _camera_thread(self):
        """Background camera processing for overlay mode."""
        cap = cv2.VideoCapture(0)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

        log.info("Camera started (background)")
        
        # Dedicated thread to keep OpenCV buffer empty (zero latency)
        self._latest_frame = None
        
        def frame_reader():
            while self.running:
                ret, f = cap.read()
                if ret:
                    self._latest_frame = f
                else:
                    time.sleep(0.005)
                    
        reader_thread = threading.Thread(target=frame_reader, daemon=True)
        reader_thread.start()

        while self.running:
            frame = self._latest_frame
            self._latest_frame = None
            
            if frame is None:
                time.sleep(0.005)
                continue

            frame = cv2.flip(frame, 1)

            # Process frame
            event, _, cursor_pos, landmarks_norm, current_gesture, confidence = self.detector.process_frame(
                frame, draw=False
            )
            
            # Real-time terminal telemetry
            in_range = "YES" if landmarks_norm else "NO "
            shape = current_gesture.ljust(10) if current_gesture else "NONE      "
            cx = f"{cursor_pos[0]:.3f}"
            cy = f"{cursor_pos[1]:.3f}"
            conf = f"{confidence:.2f}"
            print(f"\r[TELEMETRY] In Range: {in_range} | Shape: {shape} | Pos: ({cx}, {cy}) | Conf: {conf}   ", end="", flush=True)

            # Move cursor (this uses Win32 API, safe from any thread)
            if self.cursor and landmarks_norm:
                if current_gesture == "SCROLL":
                    self.cursor.scroll_mode(cursor_pos[0], cursor_pos[1])
                else:
                    self.cursor.move(cursor_pos[0], cursor_pos[1])
            elif self.cursor:
                self.cursor.reset()

            # Store data for main thread to draw
            with self._data_lock:
                self._overlay_data["landmarks"] = landmarks_norm
                # Show the continuous gesture or the specific event on the subtle top text
                self._overlay_data["event"] = event or current_gesture
                self._overlay_data["is_pinching"] = event in ("PINCH_START", "PINCH_HOLD")
                self._overlay_data["is_grabbing"] = (current_gesture == Gesture.GRAB)
                self._overlay_data["dirty"] = True

            # Handle gesture events (network calls are thread-safe)
            if event:
                self._handle_gesture_event(event)

        cap.release()

    # ---- Normal camera mode ----
    def _run_camera(self):
        """Run with OpenCV camera window (original mode)."""
        cap = cv2.VideoCapture(0)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

        log.info("Camera started. FIST=GRAB | OPEN HAND=DROP | PINCH=click | Q=quit")
        frame_skip = 0

        while self.running:
            ret, frame = cap.read()
            if not ret:
                continue

            frame_skip += 1
            if frame_skip % 2 != 0:
                continue

            frame = cv2.flip(frame, 1)
            event, annotated, cursor_pos, landmarks_norm = self.detector.process_frame(
                frame, draw=self.show_camera
            )

            if event:
                self._handle_gesture_event(event)

            if self.show_camera:
                self._draw_hud(annotated)
                cv2.imshow("GestureSync -- Windows", annotated)

                key = cv2.waitKey(1) & 0xFF
                if key == ord('q'):
                    self.running = False
                    break
                elif key == ord('h'):
                    self.show_camera = not self.show_camera

        cap.release()
        cv2.destroyAllWindows()

    def _draw_hud(self, frame):
        h, w = frame.shape[:2]
        overlay = frame.copy()
        cv2.rectangle(overlay, (0, h - 60), (w, h), (0, 0, 0), -1)
        cv2.addWeighted(overlay, 0.5, frame, 0.5, 0, frame)

        connected = "CONNECTED" if self.ws else "DISCONNECTED"
        color = (0, 200, 0) if self.ws else (0, 0, 200)
        cv2.putText(frame, f"Server: {connected}",
                    (10, h - 35), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)
        cv2.putText(frame, "FIST=GRAB | OPEN=DROP | PINCH=click | Q=quit",
                    (10, h - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (180, 180, 180), 1)


# ------------------------------------------------------------------ #
#  Entry point                                                         #
# ------------------------------------------------------------------ #

def main():
    parser = argparse.ArgumentParser(description="GestureSync Windows Client")
    parser.add_argument(
        "--server",
        default="ws://localhost:8765/ws/windows",
        help="Relay server WebSocket URL"
    )
    parser.add_argument(
        "--overlay",
        action="store_true",
        help="Run in overlay mode (transparent hand tracking on desktop)"
    )
    args = parser.parse_args()

    mode = "OVERLAY" if args.overlay else "NORMAL"
    log.info(f"GestureSync Windows Client starting [{mode}]...")
    client = WindowsGestureClient(
        server_url=args.server,
        overlay_mode=args.overlay,
    )
    client.start()


if __name__ == "__main__":
    main()

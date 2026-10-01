# GestureSync 🤜📱

**Air Gesture Cross-Device Drag & Drop** — Grab content on Windows with a fist gesture, drop it on Android by opening your hand.

---

## Project Structure

```
gesture-sync/
├── shared/
│   └── gesture_core.py        # MediaPipe gesture detection (shared logic)
├── server/
│   ├── server.py              # FastAPI WebSocket relay server
│   └── requirements.txt
├── windows/
│   ├── windows_client.py      # Windows gesture + clipboard client
│   └── requirements.txt
└── android/
    └── app/
        ├── build.gradle       # Dependencies (CameraX, MediaPipe, OkHttp)
        └── src/main/
            ├── AndroidManifest.xml
            ├── java/com/gesturesync/
            │   ├── MainActivity.kt        # UI + permission handling
            │   └── GestureDropService.kt  # Foreground service (camera + gestures + WS)
            └── res/layout/
                └── activity_main.xml      # Dark-themed UI
```

---

## Quick Start

### Step 1 — Start the Relay Server

```bash
cd server
pip install -r requirements.txt
uvicorn server:app --host 0.0.0.0 --port 8765
```

> Find your PC's LAN IP: `ipconfig` → look for IPv4 address (e.g. `192.168.1.100`)

### Step 2 — Start Windows Client

```bash
cd windows
pip install -r requirements.txt
python windows_client.py --server ws://localhost:8765/ws/windows
```

### Step 3 — Build & Install Android App

1. Open `android/` folder in **Android Studio**
2. Download [hand_landmarker.task](https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task) → place in `android/app/src/main/assets/`
3. Build & run on your Android device
4. Enter server URL: `ws://192.168.1.100:8765/ws/android` (use your PC's IP)
5. Tap **▶ START**

---

## How to Use

```
1. ✊  Make FIST on PC (hold 0.4s) → Clipboard content grabbed & sent to server
2. 📱  On Android → make OPEN HAND gesture → Content received!
3. ✅  Content automatically copied to Android clipboard
```

---

## Gesture Reference

| Gesture | Platform | Action |
|---|---|---|
| ✊ Fist (hold 0.4s) | Windows | GRAB — capture clipboard |
| 🖐️ Open hand (after fist) | Android | DROP — receive content |

---

## Content Types Supported

| Type | Windows Source | Android Target |
|---|---|---|
| **Text** | Clipboard text | Auto-copied to clipboard |
| **URL** | Copied URL | Copied to clipboard |

---

## Requirements

### Windows
- Python 3.9+
- Webcam (front-facing preferred)
- Same WiFi as Android

### Android
- Android 8.0+ (API 26)
- Front camera
- Same WiFi as PC

### Server
- Can run on Windows PC itself
- Needs to be reachable from Android on same network

---

## Architecture

```
[Windows PC]                    [Relay Server]              [Android Phone]
   Camera                         FastAPI                      Camera
     ↓                              ↓                             ↓
gesture_core.py  →  GRAB  →   WebSocket  →  CONTENT_READY  →  CameraX
     ↓                         relay                         MediaPipe
Clipboard grab                    ↓                             ↓
     ↓             ←  DROP  ←  WebSocket  ←  DROP gesture  ←  FIST→OPEN
Content sent                      ↓
                              RECEIVE → Android clipboard ✅
```

---

## Troubleshooting

| Issue | Fix |
|---|---|
| Camera not detected | Check camera index in `cv2.VideoCapture(0)` — try `1` or `2` |
| Android can't connect | Make sure PC and phone are on same WiFi |
| Gestures not detected | Improve lighting; keep hand 30–60cm from camera |
| `win32clipboard` error | Run `pip install pywin32` and restart |

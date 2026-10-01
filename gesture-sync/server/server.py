"""
server.py -- GestureSync Relay Server
FastAPI WebSocket relay between devices (PC, Android, etc.)
Supports text clipboard AND file transfer via HTTP upload/download.

Run:
    pip install fastapi uvicorn python-multipart
    uvicorn server:app --host 0.0.0.0 --port 8765 --reload

Connect:
    Device 1 -> ws://YOUR_LAN_IP:8765/ws/pc1
    Device 2 -> ws://YOUR_LAN_IP:8765/ws/android
"""

import json
import time
import asyncio
import uuid
import os
import shutil
import logging
from typing import Optional, Dict

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, Form
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger("GestureSync")

app = FastAPI(title="GestureSync Relay Server", version="2.0.0")

# Temp storage for uploaded files
UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "_transfers")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Max file size for WebSocket inline transfer (base64): 2 MB
WS_INLINE_MAX = 2 * 1024 * 1024


# ------------------------------------------------------------------ #
#  State                                                               #
# ------------------------------------------------------------------ #

class RelayState:
    def __init__(self):
        self.clients: Dict[str, WebSocket] = {}   # device_id -> ws
        self.clipboard: Optional[dict]     = None  # last grabbed payload
        self.grab_time: Optional[float]    = None
        self.grab_source: Optional[str]    = None  # who grabbed

state = RelayState()


# ------------------------------------------------------------------ #
#  HTTP Routes                                                         #
# ------------------------------------------------------------------ #

@app.get("/", response_class=HTMLResponse)
async def status_page():
    connected = list(state.clients.keys())
    payload_type = state.clipboard.get("type") if state.clipboard else "--"
    payload_name = ""
    if state.clipboard and state.clipboard.get("filename"):
        payload_name = state.clipboard["filename"]
    return f"""
    <html><body style="font-family:monospace;background:#111;color:#0f0;padding:2rem">
    <h2>GestureSync Server v2</h2>
    <p>Connected devices : {connected}</p>
    <p>Clipboard payload : {payload_type} {payload_name}</p>
    <p>Grabbed by        : {state.grab_source or '--'}</p>
    <p>Server time       : {time.strftime('%H:%M:%S')}</p>
    </body></html>
    """


@app.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    device_id: str = Form("unknown"),
):
    """
    Upload a file for transfer. Returns a file_id that receivers
    can use to download via GET /download/{file_id}.
    """
    file_id = str(uuid.uuid4())[:8]
    # Preserve original filename
    safe_name = os.path.basename(file.filename or "file")
    dest_dir = os.path.join(UPLOAD_DIR, file_id)
    os.makedirs(dest_dir, exist_ok=True)
    dest_path = os.path.join(dest_dir, safe_name)

    with open(dest_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    file_size = os.path.getsize(dest_path)
    log.info(f"Uploaded: {safe_name} ({file_size} bytes) -> file_id={file_id}")

    # Store in clipboard state
    state.clipboard = {
        "type":     "file",
        "filename": safe_name,
        "file_id":  file_id,
        "size":     file_size,
        "source":   device_id,
        "ts":       time.time(),
    }
    state.grab_time = time.time()
    state.grab_source = device_id

    # Notify all OTHER connected devices
    await _broadcast_content_ready(device_id)

    return JSONResponse({
        "ok": True,
        "file_id": file_id,
        "filename": safe_name,
        "size": file_size,
    })


@app.get("/download/{file_id}")
async def download_file(file_id: str):
    """Download a previously uploaded file by its ID."""
    dest_dir = os.path.join(UPLOAD_DIR, file_id)
    if not os.path.isdir(dest_dir):
        return JSONResponse({"error": "File not found"}, status_code=404)

    files = os.listdir(dest_dir)
    if not files:
        return JSONResponse({"error": "File not found"}, status_code=404)

    file_path = os.path.join(dest_dir, files[0])
    return FileResponse(file_path, filename=files[0])


# ------------------------------------------------------------------ #
#  WebSocket                                                           #
# ------------------------------------------------------------------ #

@app.websocket("/ws/{device_id}")
async def websocket_endpoint(websocket: WebSocket, device_id: str):
    """
    Connect any device. device_id can be anything: 'pc1', 'android', 'laptop2', etc.
    """
    await websocket.accept()
    state.clients[device_id] = websocket
    log.info(f"[+] {device_id} connected  (online: {list(state.clients.keys())})")

    await _broadcast_status()

    try:
        while True:
            raw  = await websocket.receive_text()
            data = json.loads(raw)
            event = data.get("event")

            log.info(f"<< {device_id} : {event}")

            # ---- GRAB: device captured content ----
            if event == "GRAB":
                content_type = data.get("content_type", "text")

                if content_type == "file" and data.get("file_id"):
                    # File was already uploaded via HTTP, just store reference
                    state.clipboard = {
                        "type":     "file",
                        "filename": data.get("filename", "file"),
                        "file_id":  data["file_id"],
                        "size":     data.get("size", 0),
                        "source":   device_id,
                        "ts":       time.time(),
                    }
                else:
                    # Inline content (text, URL, small data)
                    state.clipboard = {
                        "type":    content_type,
                        "content": data.get("content", ""),
                        "source":  device_id,
                        "ts":      time.time(),
                    }

                state.grab_time = time.time()
                state.grab_source = device_id
                log.info(f"Stored payload: {state.clipboard['type']}"
                         f" from {device_id}")

                # ACK back to sender
                await _send(websocket, {"event": "GRAB_ACK", "ok": True})

                # Notify all OTHER devices
                await _broadcast_content_ready(device_id)

            # ---- DROP: device wants the content ----
            elif event == "DROP":
                if state.clipboard:
                    response = {
                        "event": "RECEIVE",
                        "type":  state.clipboard["type"],
                    }
                    if state.clipboard["type"] == "file":
                        # Send download URL instead of inline content
                        file_id = state.clipboard.get("file_id", "")
                        response["file_id"]  = file_id
                        response["filename"] = state.clipboard.get("filename", "file")
                        response["size"]     = state.clipboard.get("size", 0)
                        response["download_url"] = f"/download/{file_id}"
                    else:
                        response["content"] = state.clipboard.get("content", "")

                    await _send(websocket, response)
                    log.info(f">> Delivered to {device_id}: "
                             f"{state.clipboard['type']}")
                    state.clipboard = None
                else:
                    await _send(websocket, {
                        "event":   "RECEIVE_EMPTY",
                        "message": "No content available",
                    })

            # ---- PING (keep-alive) ----
            elif event == "PING":
                await _send(websocket, {"event": "PONG", "ts": time.time()})

    except WebSocketDisconnect:
        log.warning(f"[-] {device_id} disconnected")
        state.clients.pop(device_id, None)
        await _broadcast_status()
    except Exception as e:
        log.error(f"[!] Error from {device_id}: {e}")
        state.clients.pop(device_id, None)


# ------------------------------------------------------------------ #
#  Helpers                                                             #
# ------------------------------------------------------------------ #

async def _send(ws: WebSocket, payload: dict):
    try:
        await ws.send_text(json.dumps(payload))
    except Exception:
        pass


async def _broadcast_status():
    status = {
        "event":   "STATUS",
        "devices": list(state.clients.keys()),
    }
    for ws in state.clients.values():
        await _send(ws, status)


async def _broadcast_content_ready(source_device: str):
    """Notify all devices EXCEPT the source that content is available."""
    if not state.clipboard:
        return
    for device_id, ws in state.clients.items():
        if device_id == source_device:
            continue
        msg = {
            "event":   "CONTENT_READY",
            "type":    state.clipboard["type"],
            "source":  source_device,
            "message": f"Content grabbed from {source_device}!",
        }
        if state.clipboard["type"] == "file":
            msg["filename"] = state.clipboard.get("filename", "")
            msg["size"]     = state.clipboard.get("size", 0)
        await _send(ws, msg)

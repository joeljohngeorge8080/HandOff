"""Constants. Limits here are contract values from docs/ (ADR-017, ADR-055)."""

from __future__ import annotations

# ADR-055 (Phase 2): .mp4 and .exe are no longer accepted.
ALLOWED_EXTENSIONS: frozenset[str] = frozenset({".txt", ".jpg", ".jpeg", ".png", ".pdf"})

# ADR-017: individual files are limited to 50 MB; a file of exactly 50 MB is accepted.
MAX_FILE_SIZE: int = 50 * 1024 * 1024

# SECURITY §23: bound the size of a transfer request.
MAX_FILES_PER_TRANSFER: int = 100
# Auto-open (ADR-061): a large transfer must not open a hundred windows.
MAX_AUTO_OPEN_FILES: int = 5

# SECURITY §46: single, centralized port constant (advertised via discovery in M2).
PEER_PORT: int = 8765

# DATABASE §34: 0 means "forever".
DEFAULT_HISTORY_RETENTION_DAYS: int = 90
HISTORY_CLEANUP_INTERVAL_SECONDS: float = 3600.0

# Streaming chunk size for hashing and copying (never load whole files into memory).
IO_CHUNK_SIZE: int = 1024 * 1024

# Local IPC: reject absurdly large request lines.
MAX_IPC_LINE_BYTES: int = 1024 * 1024

# Receiver-side bound on names inside a transfer.
MAX_FILENAME_BYTES: int = 255

# ----- Networking (M2) -------------------------------------------------------------------

API_VERSION = "v1"
SERVICE_TYPE = "_handoff._tcp.local."

# Signed requests (API §43.1): maximum clock difference and how long nonces are remembered.
SIGNATURE_MAX_SKEW_SECONDS = 60
NONCE_TTL_SECONDS = 2 * SIGNATURE_MAX_SKEW_SECONDS

# JSON bodies on the peer API are small; the manifest of 100 files is ~30 KB.
MAX_JSON_BODY_BYTES = 1024 * 1024

# Peer health / offline detection (FR-015).
HEALTH_INTERVAL_SECONDS = 3.0
HEALTH_FAILURE_THRESHOLD = 3

# Receiver: an accepted transfer that never receives data is failed after this long.
ACCEPT_TIMEOUT_SECONDS = 60.0
UPLOAD_STALL_TIMEOUT_SECONDS = 60.0

# SECURITY §38: basic protection against floods of invalid requests.
FAILURE_LIMIT = 20
FAILURE_WINDOW_SECONDS = 60.0

# HTTP client timeouts for peer calls.
CONNECT_TIMEOUT_SECONDS = 5.0
REQUEST_TIMEOUT_SECONDS = 15.0
UPLOAD_RESPONSE_TIMEOUT_SECONDS = 180.0

# ----- Hand control (ADR-056) -------------------------------------------------------------

# Pinch = thumb-tip to index-tip distance divided by hand size (wrist to middle MCP).
CV_PINCH_ON = 0.26  # fingers closer than this press the button (measured closed: 0.16-0.23)
CV_PINCH_OFF = 0.32  # fingers further apart than this (for CV_RELEASE_FRAMES) release it
CV_RELEASE_FRAMES = 2
# ...and closer than CV_PINCH_ON in this many camera frames in a row to press. Measured: tracking
# jitter dips the pinch for a single frame, real clicks hold it 80-270 ms (3+ frames).
CV_PRESS_FRAMES = 2
CV_LOST_GRACE_SECONDS = 0.30  # hand missing longer than this counts as lost
CV_DRAG_RADIUS_PX = 12  # moving this far while pressed makes it a drag (Esc on loss)

# One-euro filter on the target, then exponential follow in the control loop.
# Inputs are in hand sizes (ADR-065), so a brisk movement is ~2-10 units/s; beta makes the
# cutoff rise with speed (1.5 here = the 8.0 that measured ~13 ms of lag in camera units).
CV_MIN_CUTOFF = 1.5
CV_BETA = 1.5
CV_D_CUTOFF = 1.0
CV_FOLLOW_TAU = 0.020
CV_CONTROL_HZ = 125

CV_CAMERA_INDEX = 0
CV_CAMERA_SIZE = (640, 480)
CV_CAMERA_FPS = 60
# Driver frame buffers. 1 halves the frame rate (the camera skips every frame that arrives while
# the previous one is being processed); 2 keeps the full rate with at most one frame queued.
CV_CAMERA_BUFFERS = 2
# MediaPipe's options, as its source defines them (ADR-065): detection gates the palm detector;
# presence gates the landmark model's "a hand is still here" score on every tracked frame (a
# fist hides the fingers and this score dips, which dropped the hand mid-grab, so it is low);
# tracking is only the overlap threshold that merges a re-detected box with the tracked one.
CV_MIN_DETECTION_CONFIDENCE = 0.5
CV_MIN_PRESENCE_CONFIDENCE = 0.3
CV_MIN_TRACKING_CONFIDENCE = 0.5

# Worker -> core pipe: line cap, event rate cap, and how often status is repeated.
CV_MAX_LINE_BYTES = 4096
CV_MAX_EVENTS_PER_SEC = 20
CV_STATUS_INTERVAL_SECONDS = 1.0

# Model fetched at build time (scripts/fetch-hand-model.sh); never downloaded at runtime.
CV_MODEL_FILENAME = "hand_landmarker.task"

# Touchpad-style pointer (ADR-056, decision 9): the cursor moves by the *change* in position
# times a gain that grows with speed. Since ADR-065 the position is the index fingertip, measured
# in hand sizes (wrist to middle knuckle; ~0.19 camera-widths at a desk), so the pointer feels the
# same near or far from the camera. Gains: screen-widths per hand size; speeds: hand sizes/s.
CV_GAIN_SLOW = 0.15  # fine control: a 1 cm fingertip move is ~35 px on a 1920 px screen
CV_GAIN_FAST = 1.1
CV_SPEED_SLOW = 0.8
CV_SPEED_FAST = 4.5
CV_MAX_STEP = 1.5  # one-frame jumps larger than this (hand sizes) are tracking glitches
# A pinch moves the fingertip toward the thumb. So the fingertip only steers while the pinch is
# open; closing it hands steering to the palm, and a fast-changing pinch (a click) steers with the
# palm alone. Clicks land where the cursor was, a drag still follows the hand.
CV_HAND_WIDTHS = 0.19  # a typical hand size in camera-widths, for detections without one
CV_TIP_FULL_PINCH = 0.7  # pinch ratio at and above which the fingertip steers fully
CV_PINCH_SPEED_GATE = 2.5  # pinch ratio change per second above which the fingertip is ignored
CV_REFERENCE_GAP_SECONDS = 0.15  # hand missing this long = lifted: re-reference, do not jump
CV_SPEED_SMOOTHING = 0.4

# Pose classification (ADR-064). A finger's bend is the sum of its three joint angles in degrees
# (0 = straight), from the landmarks' 3D positions, so it does not depend on which way the hand
# faces. Measured on a real hand: open fingers < 65, a fist 100-260, the gap is "neither".
CV_BEND_EXTENDED = 70.0
CV_BEND_CURLED = 85.0
# A pinch click curls the index far less than the other fingers; a fist curls them alike. So with
# the thumb on the index, it is a pinch only when the index is this much straighter than the rest.
CV_PINCH_INDEX_MARGIN = 60.0
# Scroll pose: index and middle straight and side by side (tips closer than this x hand size).
CV_SCROLL_TIPS_RATIO = 0.5
CV_POINT_ON_SECONDS = 0.04  # a pointing pose must hold this long before the cursor follows
CV_POINT_OFF_SECONDS = 0.10  # ...and a lost pose must persist this long before it stops
# A hand closing into a fist passes through frames that look like a pinch (thumb on the curled
# index). Pointing cannot start this soon after an open palm or fist, so a grab never clicks.
CV_POINT_AFTER_PALM_SECONDS = 0.30

# COPY gesture: open palm -> closed palm (grab, copies the selection), then closed -> open
# palm (release, sends it to the connected peer).
# A pose must dominate a window this long, covering CV_PALM_AGREE of it (so one flickering frame
# does not reset it). Measured: casual hand closures last ~0.5 s, a deliberate fist 0.9 s or more,
# and their shapes are the same, so only the fist's duration tells them apart.
CV_PALM_OPEN_HOLD_SECONDS = 0.35
CV_PALM_FIST_HOLD_SECONDS = 0.8  # x CV_PALM_AGREE = ~0.56 s of fist: above a casual ~0.5 s
CV_PALM_AGREE = 0.7
CV_GRAB_WINDOW_SECONDS = 1.5  # the fist must follow the open palm within this
CV_HOLD_MAX_SECONDS = 20.0  # a grab that is never released expires
# After a release, no new grab until this has passed. 2.0 s swallowed a deliberate second grab
# (measured): its fist then opened into a release that nobody meant.
CV_COPY_COOLDOWN_SECONDS = 0.8
CV_COPY_SETTLE_SECONDS = 0.15  # time the file manager gets to fill the clipboard after Ctrl+C
CV_CLIPBOARD_TIMEOUT_SECONDS = 2.0

# Two-finger scroll (ADR-064): in the scroll pose the cursor stays put and moving the hand up or
# down turns the mouse wheel, like two fingers on a touchpad.
CV_SCROLL_ON_SECONDS = 0.10  # the pose must hold this long before scrolling starts
CV_SCROLL_OFF_SECONDS = 0.20  # ...and be gone this long before it stops
# Wheel steps per frame-height of fingertip movement grow with speed (frame-heights per second),
# like a touchpad: a quick flick scrolls far, the slow return of the fingers scrolls little back.
CV_SCROLL_STEPS_SLOW = 12.0
CV_SCROLL_STEPS_FAST = 80.0
CV_SCROLL_SPEED_SLOW = 0.3
CV_SCROLL_SPEED_FAST = 1.5
CV_SCROLL_NATURAL = True  # True: hand up scrolls down (content follows the hand), as touchpads

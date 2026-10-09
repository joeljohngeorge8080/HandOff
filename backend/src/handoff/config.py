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
CV_PINCH_ON = 0.22  # fingers closer than this press the button
CV_PINCH_OFF = 0.32  # fingers further apart than this (for CV_RELEASE_FRAMES) release it
CV_RELEASE_FRAMES = 2
CV_LOST_GRACE_SECONDS = 0.30  # hand missing longer than this counts as lost
CV_DRAG_RADIUS_PX = 12  # moving this far while pressed makes it a drag (Esc on loss)

# One-euro filter on the target, then exponential follow in the control loop.
CV_MIN_CUTOFF = 1.5
CV_BETA = 0.012
CV_D_CUTOFF = 1.0
CV_FOLLOW_TAU = 0.020
CV_CONTROL_HZ = 125

CV_CAMERA_INDEX = 0
CV_CAMERA_SIZE = (640, 480)
CV_CAMERA_FPS = 60

# Worker -> core pipe: line cap, event rate cap, and how often status is repeated.
CV_MAX_LINE_BYTES = 4096
CV_MAX_EVENTS_PER_SEC = 20
CV_STATUS_INTERVAL_SECONDS = 1.0

# Model fetched at build time (scripts/fetch-hand-model.sh); never downloaded at runtime.
CV_MODEL_FILENAME = "hand_landmarker.task"

# Touchpad-style pointer (ADR-056, decision 9): the cursor moves by the *change* in hand
# position times a gain that grows with hand speed. Gains are in screen-widths per camera-width;
# speeds in camera-widths per second. Starting values: tune on a real hand.
CV_GAIN_SLOW = 1.2
CV_GAIN_FAST = 6.0
CV_SPEED_SLOW = 0.10
CV_SPEED_FAST = 0.80
CV_MAX_STEP = 0.25  # one-frame jumps larger than this (camera-widths) are tracking glitches
CV_REFERENCE_GAP_SECONDS = 0.15  # hand missing this long = lifted: re-reference, do not jump
CV_SPEED_SMOOTHING = 0.4

# Pose classification (ADR-057). A finger is "extended" when its tip is this much further from
# the wrist than its middle joint. Starting values: tune on a real hand.
CV_FINGER_EXTENDED_RATIO = 1.15
CV_PINCH_INDEX_RATIO = 0.95  # a pinch with a curled index still points above this reach
CV_POINT_ON_SECONDS = 0.04  # a pointing pose must hold this long before the cursor follows
CV_POINT_OFF_SECONDS = 0.10  # ...and a lost pose must persist this long before it stops

# COPY gesture: open palm -> closed palm (grab, copies the selection), then closed -> open
# palm (release, sends it to the connected peer).
CV_PALM_HOLD_SECONDS = 0.25  # a palm / fist pose must be stable this long to count
CV_GRAB_WINDOW_SECONDS = 1.5  # the fist must follow the open palm within this
CV_HOLD_MAX_SECONDS = 20.0  # a grab that is never released expires
CV_COPY_COOLDOWN_SECONDS = 2.0  # after a release, no new grab until this has passed
CV_COPY_SETTLE_SECONDS = 0.15  # time the file manager gets to fill the clipboard after Ctrl+C
CV_CLIPBOARD_TIMEOUT_SECONDS = 2.0

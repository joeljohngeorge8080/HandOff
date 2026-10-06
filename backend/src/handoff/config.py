"""Constants. Limits here are contract values from docs/ (ADR-017, ADR-055)."""

from __future__ import annotations

# ADR-055 (Phase 2): .mp4 and .exe are no longer accepted.
ALLOWED_EXTENSIONS: frozenset[str] = frozenset({".txt", ".jpg", ".jpeg", ".png", ".pdf"})

# ADR-017: individual files are limited to 50 MB; a file of exactly 50 MB is accepted.
MAX_FILE_SIZE: int = 50 * 1024 * 1024

# SECURITY §23: bound the size of a transfer request.
MAX_FILES_PER_TRANSFER: int = 100

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

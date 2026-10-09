# API

> **Phase 3 amendment (ADR-056).** The CV channel of §38-§42 is a **local pipe** between the core and its `--cv-worker` child process (JSON lines on the worker's stdout; the worker exits when its stdin closes). `POST /internal/v1/cv/events` stays **disabled** and nothing CV-related is exposed on the LAN. New local IPC: `cv.status` -> `{state: off|starting|tracking|no_hand|error, message?}`; `settings.set` accepts key `hand_control_enabled` (boolean; anything else is `INVALID_REQUEST`) and starts/stops the worker; `status.snapshot` carries `hand_control`. New core-to-UI push events: `cv.status` and `cv.event` (`{event, ...}` with a canonical ADR-052 name, only `gesture_detected` / `direction_detected` are forwarded; ADR-057: the worker's `grab` / `release` lines are consumed by the core, which reads the OS clipboard and calls `drop.send`, and reports `copied` / `copy_empty` / `copy_failed` / `copy_cancelled` / `claimed` / `claim_failed` / `sent` / `send_failed` as `gesture_detected`). **ADR-058:** `grab` holds on the grabbing device; `release` there only cancels; on the other device it sends the signed `POST /api/v1/handoff/claim` (§20a) to its connected peer, whose answer starts the transfer. New error codes (IPC only): `CV_UNAVAILABLE` (dependency or model missing), `CV_UNSUPPORTED_SESSION` (native Wayland), `CV_CAMERA_UNAVAILABLE`, `CLIPBOARD_UNAVAILABLE` (internal, only reported as `copy_failed`).

> **Phase 2 amendment (ADR-055) — breaking.** Removed: `GET /api/v1/receive-mode` (§13), the `receive_mode` field of `GET /api/v1/device`, the Receive Mode check in §15/§16, the IPC actions `receive_mode.get` / `receive_mode.set`, and the error code `RECEIVE_MODE_DISABLED`. Allowed extensions are `.txt .jpg .jpeg .png .pdf`. The receiver writes to its own `receive_directory`; an invalid one returns `RECEIVER_NOT_READY`. New local IPC actions (never exposed on the LAN): `drop.inspect`, `drop.send`, and `settings.set` with key `receive_directory`. New core-to-UI push events: `transfer.updated`, `connection.changed`. Peers must run 0.2.x or later.

> **Phase 2 local IPC additions (ADR-054; never exposed on the LAN).**
>
> `drop.inspect {paths[]}` → `{ok, file_count, total_size, items[{name, ok, size?, code?, reason?, message?}]}`. Read-only: copies and records nothing. `reason` is one of `unsupported_type, executable_content, directory, symlink, too_large, missing, invalid_name, unreadable`.
>
> `drop.send {paths[]}` → `{transfer}`. Order: connected online peer (`DEVICE_NOT_FOUND` "No HandOff device connected" / `DEVICE_OFFLINE`), no active transfer (`INVALID_STATE`), then **every** path validated (all-or-nothing; the error's `details.items` lists each verdict), then `files.import` (copy + SHA-256) and the existing `transfer.create`. The managed copies are logically deleted when the transfer is terminal. At most 100 paths.
>
> `settings.set {key:"receive_directory", value}` validates the folder (absolute, existing, writable, not inside HandOff's data directory; otherwise `INVALID_PATH`). `status.snapshot` carries `receive_directory` instead of `receive_mode`.
>
> **Push events** (stdout lines with `event` and no `id`): `transfer.updated` (the transfer as in `history.list`, progress throttled to ~4 Hz, every status change delivered) and `connection.changed` (the connection snapshot). Events only notify; the database stays the truth and `status.snapshot` can always recover a missed one.
>
> Peer response addition: `POST /transfers/{id}/data` file results may carry `saved_as` (the name written on the receiver). `name` remains the manifest name.

## 1. Purpose

This document defines the API contracts used by HandOff.

The API is divided into two communication boundaries:

```text
                         HandOff
                            │
              ┌─────────────┴─────────────┐
              │                           │
        Internal API                 Peer API
              │                           │
        Tauri ↔ Python              Device ↔ Device
```

### Internal API

Used by the Tauri desktop UI to communicate with the Python application core.

### Peer API

Used by one HandOff installation to communicate with another HandOff installation over the local Wi-Fi network.

---

# 2. API Principles

The API must follow these principles:

1. APIs must be explicit and predictable.
2. All peer APIs must be versioned.
3. All errors must use a standard error format.
4. File transfer must be streamed.
5. Binary files must never be loaded completely into memory unnecessarily.
6. Transfer operations must have unique IDs.
7. Device IDs must be persistent.
8. API responses must contain enough information for the UI to display meaningful status.
9. The API must distinguish between application errors and network failures.
10. Future computer-vision events must use a stable event contract.

---

# 3. API Version

Peer APIs use:

```text
/api/v1
```

Example:

```text
GET /api/v1/device
```

Future breaking changes must introduce a new API version rather than silently changing the existing contract.

Example:

```text
/api/v2
```

The Phase-1 implementation only exposes:

```text
/api/v1
```

---

# 4. Communication Architecture

## 4.1 Tauri → Python

The Tauri application communicates with the Python core through a local Python sidecar process.

Conceptually:

```text
┌────────────────────┐
│      Tauri UI      │
└─────────┬──────────┘
          │
          │ Local IPC
          ▼
┌────────────────────┐
│   Python Process   │
│                    │
│ Application Core   │
└────────────────────┘
```

The Python process is launched and managed by the desktop application.

The internal communication mechanism must not require exposing the Python application to the LAN.

The exact IPC implementation may use a structured stdin/stdout protocol or an equivalent local process IPC mechanism.

---

# 5. Peer API

Every running HandOff installation exposes a local HTTP API to other HandOff devices on the same network.

Example:

```text
http://192.168.1.12:<port>/api/v1/
```

The peer API is only intended for HandOff-to-HandOff communication.

---

# 6. Peer API Base URL

The logical base URL is:

```text
/api/v1
```

Example complete endpoint:

```text
GET http://192.168.1.12:8765/api/v1/device
```

The port must be configurable internally and advertised through service discovery.

---

# 7. Device Discovery

HandOff uses **mDNS** for local-network device discovery.

Each running HandOff instance advertises a service.

Conceptually:

```text
_service:
_handoff._tcp.local
```

The advertised metadata should include enough information to identify the device and locate its API.

Required logical metadata:

```text
device_id
device_name
api_port
api_version
```

The IP address is discovered from the mDNS result and must not be treated as the permanent device identity.

---

# 8. Device Information

## GET `/api/v1/device`

Returns information about the HandOff installation.

### Request

```http
GET /api/v1/device
```

### Response

```json
{
  "device_id": "7e7d8c2a-5e9e-4e1c-9a7d-9a1c4e1f7a31",
  "device_name": "Joel-Laptop",
  "api_version": "v1",
  "platform": "linux",
  "receive_mode": true,
  "status": "available"
}
```

### Fields

| Field | Type | Description |
|---|---|---|
| `device_id` | string | Persistent unique device identifier |
| `device_name` | string | Human-readable device name |
| `api_version` | string | Supported peer API version |
| `platform` | string | Operating system |
| `receive_mode` | boolean | Whether the device currently accepts files |
| `status` | string | Device availability |

---

# 9. Health Check

## GET `/api/v1/health`

Used to determine whether the peer is reachable and responding.

### Response

```json
{
  "status": "ok",
  "device_id": "7e7d8c2a-5e9e-4e1c-9a7d-9a1c4e1f7a31"
}
```

A failed health check must not be interpreted as proof that the device is permanently offline. The connection manager should apply a reasonable retry/timeout policy.

---

# 10. Connection Model

A connection is represented logically as:

```json
{
  "device_id": "7e7d8c2a-5e9e-4e1c-9a7d-9a1c4e1f7a31",
  "device_name": "Aaron-Laptop",
  "status": "connected"
}
```

Possible states:

```text
available
connecting
connected
offline
```

Phase 1 permits exactly one connected peer.

---

# 11. Connect Device

## POST `/api/v1/connection`

Sent by the initiating device to a discovered peer to establish the logical connection.

The request carries the **initiator's** own identity. It must be signed (see §43.1).

### Request

```json
{
  "device_id": "3b1f6c0e-8a52-4c7e-9d11-2f6a7c9e0b44",
  "device_name": "Joel-Laptop",
  "public_key": "<base64 Ed25519 public key>",
  "platform": "linux",
  "port": 8765
}
```

`port` is the port on which the initiator's own peer API listens, so the peer can reach it back.
`public_key` must be the raw 32-byte Ed25519 key, base64-encoded. If the device ID is already known, the key must match the stored one; a changed key is rejected (`DEVICE_NOT_TRUSTED`).

If this device is already connected to a *different* device that is online, the request is rejected with `DEVICE_ALREADY_CONNECTED`.

### Response

```json
{
  "connection_id": "conn_01JABC123",
  "device_id": "7e7d8c2a-5e9e-4e1c-9a7d-9a1c4e1f7a31",
  "device_name": "Aaron-Laptop",
  "public_key": "<base64 Ed25519 public key>",
  "status": "connected"
}
```

Each side stores the other's `device_id`, `device_name` and `public_key` and marks it `is_trusted = true` (ADR-048).

No remote user approval is required in Phase 1.

The user explicitly initiating the connection establishes the trust relationship.

---

# 12. Connected Peer

## GET `/api/v1/connection`

Returns the currently connected peer.

### Response

```json
{
  "connected": true,
  "device": {
    "device_id": "7e7d8c2a-5e9e-4e1c-9a7d-9a1c4e1f7a31",
    "device_name": "Aaron-Laptop",
    "address": "192.168.1.15",
    "port": 8765,
    "status": "connected"
  }
}
```

If no peer is connected:

```json
{
  "connected": false,
  "device": null
}
```

---

# 12.1 Release Connection

## DELETE `/api/v1/connection`

Sent by a device that is switching to another peer (ADR-047). It is an **internal** operation; there is no user-facing Disconnect button in Phase 1.

The receiving peer clears its active-peer state. Trust (`is_trusted`) is not removed. The request is best effort: the sender proceeds with the switch even if the old peer is unreachable.

A switch must be rejected locally (`INVALID_STATE`) while a transfer is not in a terminal state.

---

# 13. Receive Mode

## GET `/api/v1/receive-mode`

Returns the current receive state. Read-only for peers.

### Response

```json
{
  "enabled": true
}
```

Receive Mode is **changed only locally by the user** through the internal API (`receive_mode.set`). The peer API has no `PUT /receive-mode`: a remote device must never change another device's receive state (ADR-012, ADR-053).

When enabled, the device automatically accepts valid transfers from **trusted** peers.

When disabled, incoming transfers must be rejected with `RECEIVE_MODE_DISABLED` (HTTP 409).

---

# 14. Transfer Architecture

A transfer is represented by a unique transfer ID.

A transfer has the following conceptual lifecycle:

```text
CREATED
   ↓
VALIDATING
   ↓
ACCEPTED
   ↓
TRANSFERRING
   ↓
COMPLETED
```

Failure:

```text
Any state
   ↓
FAILED
```

For a multi-file job:

```text
Some files succeed
Some files fail
        ↓
PARTIALLY_COMPLETED
```

---

# 15. Transfer Creation

## Internal: `transfer.create`

The UI sends **file IDs only** (ADR-050). The backend reads the stored name, size and SHA-256 of each file.

```json
{
  "action": "transfer.create",
  "file_ids": ["file_001", "file_002"],
  "destination_device_id": "7e7d8c2a-5e9e-4e1c-9a7d-9a1c4e1f7a31"
}
```

## Peer: POST `/api/v1/transfers`

Sent by the sender's backend to the receiver. It carries the transfer **manifest** (ADR-051).

The sender must create the transfer before uploading its data. Send an `Idempotency-Key` header.

### Request

```json
{
  "transfer_id": "tr_01JABC789",
  "source_device_id": "3b1f6c0e-8a52-4c7e-9d11-2f6a7c9e0b44",
  "file_count": 2,
  "total_size": 18432000,
  "archive_name": "handoff-transfer-01.zip",
  "files": [
    {
      "file_id": "file_001",
      "filename": "photo.jpg",
      "size": 123456,
      "sha256": "9f86d081884c7d659a2feaa0c55ad015..."
    }
  ]
}
```

The sender's stored hash is authoritative. The receiver verifies every received file against the manifest.

### Response

```json
{
  "transfer_id": "tr_01JABC789",
  "status": "accepted",
  "upload_url": "/api/v1/transfers/tr_01JABC789/data"
}
```

---

# 16. Transfer Validation

Before accepting a transfer, the receiver must validate:

- Receive Mode
- Sender connection state
- Transfer ID
- File count
- Total size
- Archive format
- Archive contents
- Supported file types
- Individual file size limits

The receiver must reject invalid transfers before committing files to permanent storage.

---

# 17. Multi-File Transfer

A single transfer job represents one transfer operation.

If the user selects multiple files:

```text
photo.jpg
video.mp4
notes.txt
```

the sender packages them into a temporary archive:

```text
handoff-transfer-01.zip
│
├── photo.jpg
├── video.mp4
└── notes.txt
```

The archive is transferred as one job.

The receiver extracts the archive into its temporary transfer directory and then moves validated files into the final Received storage.

---

# 18. Archive Requirements

The archive must:

- Use a standard ZIP format.
- Preserve filenames.
- Preserve file extensions.
- Not require external software to extract.
- Be created in temporary storage.
- Be deleted after the transfer completes or fails.

The archive must not be treated as permanent user storage.

---

# 19. File Upload

## POST `/api/v1/transfers/{transfer_id}/data`

Uploads the transfer archive. Requires `Content-Type: application/zip` (`415` otherwise).

The body contains the ZIP archive as a binary stream.

Example:

```http
POST /api/v1/transfers/tr_01JABC789/data
Content-Type: application/zip
Content-Length: 18432000
```

The implementation must stream the request to disk.

It must not load the complete archive into memory.

The upload is bounded: `Content-Length` above the declared size (plus ZIP overhead) is rejected up front, and a body without `Content-Length` is cut off once it exceeds that bound (`413 FILE_TOO_LARGE`). The transfer is then marked `failed` and all temporary data is removed. An upload that makes no progress for a minute, or whose sender disconnects, is also failed.

### Successful response (`200`)

```json
{
  "transfer_id": "tr_01JABC789",
  "status": "completed",
  "files_received": 3,
  "files": [
    { "name": "photo.jpg", "status": "completed", "failure_code": null }
  ]
}
```

`status` is `completed` or `partially_completed`. In the partial case the failed files carry `"status": "failed"` and a `failure_code` such as `INVALID_HASH`.

### Failed response (`422`)

If no file passed verification, or the archive was rejected as a whole:

```json
{
  "error": {
    "code": "TRANSFER_FAILED",
    "message": "No file passed verification.",
    "details": {
      "transfer_id": "tr_01JABC789",
      "status": "failed",
      "files": [ { "name": "photo.jpg", "status": "failed", "failure_code": "INVALID_HASH" } ]
    }
  }
}
```

A structurally unsafe archive (traversal, entries not in the manifest, not a ZIP) is rejected with its specific code (`INVALID_PATH`, `TRANSFER_VALIDATION_FAILED`, `TRANSFER_ARCHIVE_INVALID`) and the transfer is `failed`.

---

# 20. Transfer Status

## GET `/api/v1/transfers/{transfer_id}`

Returns transfer progress.

### Response

```json
{
  "transfer_id": "tr_01JABC789",
  "status": "transferring",
  "file_count": 3,
  "files_completed": 2,
  "total_size": 18432000,
  "bytes_received": 12288000,
  "progress": 0.667
}
```

Possible statuses:

```text
created
validating
accepted
transferring
completed
failed
partially_completed
```

---

# 20a. Handoff Claim (hand COPY gesture, ADR-058)

## POST `/api/v1/handoff/claim`

Sent by the device the user's closed hand was carried to, to the device that grabbed. Signed like every route (API §43); the caller must be the connected trusted peer. The body is ignored: the caller is identified only by its signature.

The holder replies only if it still holds a fresh grab (at most 20 s old), then starts an ordinary transfer to the caller through its normal `drop.send` pipeline (every validation of ADR-054/055 applies) and answers right away; progress is then the usual transfer flow.

### Response `202`

```json
{ "transfer_id": "tr_01JABC789" }
```

### Errors

| Status | Code | When |
|---|---|---|
| 409 | `NOTHING_HELD` | the holder has no grab, it expired, or it was already used or cancelled |
| 404 | `DEVICE_NOT_FOUND` | the caller is not the holder's connected peer |
| 409 | `INVALID_STATE` | a transfer is already active |
| 4xx | `FILE_TYPE_NOT_SUPPORTED`, `FILE_TOO_LARGE`, `INVALID_FILE`, ... | the held files fail validation (all-or-nothing) |
| 401/403 | `INVALID_SIGNATURE`, `DEVICE_NOT_TRUSTED`, ... | unsigned or untrusted caller |

---

# 21. Transfer Progress

The Tauri UI never calls the peer API directly.

The UI obtains progress from its local Python core through the internal `status.snapshot` action (polled about once per second). The core tracks progress itself (`bytes_transferred`, `total_size`, `status`).

`GET /api/v1/transfers/{transfer_id}` is a peer endpoint used by the sender's core when it needs the receiver's view of a transfer.

---

# 22. Transfer Completion

A transfer may only be marked:

```text
completed
```

after:

1. The archive was fully received.
2. The archive passed validation.
3. The archive was successfully extracted.
4. All files were validated.
5. Files were moved into final storage.
6. Database records were successfully updated.

Only then should the receiver return success.

---

# 23. Transfer Failure

If the transfer fails:

```json
{
  "transfer_id": "tr_01JABC789",
  "status": "failed",
  "error": {
    "code": "TRANSFER_FAILED",
    "message": "The transfer could not be completed."
  }
}
```

Temporary files must be cleaned up.

---

# 24. Partial Completion

If a transfer contains multiple files and some files cannot be successfully processed:

```json
{
  "transfer_id": "tr_01JABC789",
  "status": "partially_completed",
  "files": [
    {
      "name": "photo.jpg",
      "status": "completed"
    },
    {
      "name": "video.mp4",
      "status": "completed"
    },
    {
      "name": "program.exe",
      "status": "failed"
    }
  ]
}
```

The overall transfer must not be reported as fully successful.

---

# 25. Duplicate Filename Handling

The receiver must never overwrite an existing file automatically.

Example:

```text
photo.jpg
```

already exists.

Incoming:

```text
photo.jpg
```

becomes:

```text
photo(1).jpg
```

If that exists:

```text
photo(2).jpg
```

The renaming operation must continue until a unique destination filename is found.

---

# 26. File Validation

The receiver must validate every extracted file.

Required checks:

```text
Extension allowed?
        ↓
Size <= 50 MB?
        ↓
Safe filename?
        ↓
Safe destination path?
        ↓
Valid file?
```

Only validated files may enter permanent Received storage.

---

# 27. Transfer History

Transfer history is **local** to each device and is read through the internal API, not the peer API:

```json
{ "action": "history.list", "payload": { "limit": 50, "offset": 0 } }
```

```json
{
  "items": [
    {
      "transfer_id": "tr_01JABC789",
      "direction": "sent",
      "peer_device_id": "7e7d8c2a-5e9e-4e1c-9a7d-9a1c4e1f7a31",
      "peer_device_name": "Aaron-Laptop",
      "file_count": 3,
      "total_size": 18432000,
      "archive_size": 18433100,
      "bytes_transferred": 18433100,
      "status": "completed",
      "error_code": null,
      "created_at": "2026-10-01T10:20:00Z",
      "completed_at": "2026-10-01T10:20:05Z",
      "files": [ { "name": "photo.jpg", "size": 123456, "status": "completed", "failure_code": null } ]
    }
  ]
}
```

---

# 28. Transfer Status

Peer endpoint `GET /api/v1/transfers/{transfer_id}` (see §20) lets the *sender of that transfer* read the receiver's view of it. Other devices get `404 TRANSFER_NOT_FOUND`.

The local UI reads a single transfer through `transfer.status { "transfer_id": ... }` and the running one through `status.snapshot` (§21).

---

# 29. Application File API

The internal Tauri ↔ Python API should expose file operations conceptually equivalent to:

```text
files.list
files.add
files.delete
files.get
files.select
```

The exact IPC method names may follow the Python application's implementation conventions.

Example internal request:

```json
{
  "action": "files.list"
}
```

Response:

```json
{
  "files": [
    {
      "id": "file_001",
      "name": "photo.jpg",
      "size": 4823912,
      "extension": ".jpg"
    }
  ]
}
```

---

# 30. Device API

The internal application API should expose:

```text
devices.discover
devices.list
devices.connect
devices.status
```

Example:

```json
{
  "action": "devices.discover"
}
```

Response:

```json
{
  "devices": [
    {
      "device_id": "7e7d8c2a-5e9e-4e1c-9a7d-9a1c4e1f7a31",
      "device_name": "Aaron-Laptop",
      "address": "192.168.1.15",
      "port": 8765,
      "status": "available"
    }
  ]
}
```

---

# 31. Transfer API — Internal

The UI should communicate with the Python core rather than directly with the peer device.

Conceptually:

```text
Tauri
  │
  │ start transfer
  ▼
Python Core
  │
  │ REST
  ▼
Peer
```

Example internal request:

```json
{
  "action": "transfer.create",
  "file_ids": [
    "file_001",
    "file_002"
  ],
  "destination_device_id": "device_002"
}
```

The Python core then performs the network transfer **in the background**; the action returns immediately with the new transfer (status `created`), so the UI is never blocked.

Before creating the transfer, the sender asks the peer whether Receive Mode is on (FR-023). If it is off, `transfer.create` fails at once with `RECEIVE_MODE_DISABLED` ("<name> is not accepting files") and **no transfer or history entry is created**. If the setting changes after that check, the peer rejects the transfer and it appears in history as `failed` with that code.

`transfer.create` also fails with `INVALID_STATE` if a transfer is already active, and with `DEVICE_NOT_FOUND`/`DEVICE_OFFLINE` if the destination is not the connected, online peer.

---

# 32. Standard Error Format

All API errors must use a consistent structure.

```json
{
  "error": {
    "code": "RECEIVE_MODE_DISABLED",
    "message": "The destination device is not accepting files."
  }
}
```

Optional details may be included:

```json
{
  "error": {
    "code": "FILE_TOO_LARGE",
    "message": "The file exceeds the 50 MB Phase-1 limit.",
    "details": {
      "file_name": "video.mp4",
      "size": 73400320,
      "maximum_size": 52428800
    }
  }
}
```

---

# 33. Standard Error Codes

The following codes should be used consistently.

## Device

```text
DEVICE_NOT_FOUND
DEVICE_OFFLINE
DEVICE_ALREADY_CONNECTED
PEER_CONNECTION_FAILED
INVALID_DEVICE_ID
```

## Receive

```text
RECEIVE_MODE_DISABLED
RECEIVER_NOT_READY
```

## Files

```text
FILE_NOT_FOUND
FILE_TYPE_NOT_SUPPORTED
FILE_TOO_LARGE
INVALID_FILE
FILE_STORAGE_ERROR
```

## Transfers

```text
TRANSFER_NOT_FOUND
TRANSFER_ALREADY_EXISTS
TRANSFER_FAILED
TRANSFER_INCOMPLETE
TRANSFER_VALIDATION_FAILED
TRANSFER_ARCHIVE_INVALID
TRANSFER_ARCHIVE_EXTRACTION_FAILED
```

## Network

```text
NETWORK_ERROR
REQUEST_TIMEOUT
CONNECTION_RESET
INVALID_API_VERSION
```

## Authentication and abuse protection

```text
INVALID_SIGNATURE
REPLAYED_REQUEST
DEVICE_NOT_TRUSTED
RATE_LIMITED
```

## Storage

```text
INSUFFICIENT_STORAGE
INVALID_PATH
CV_UNAVAILABLE
CV_UNSUPPORTED_SESSION
CV_CAMERA_UNAVAILABLE
INVALID_HASH
```

## Application

```text
INVALID_REQUEST
INVALID_STATE
NOTHING_HELD
INTERNAL_ERROR
```

---

# 34. HTTP Status Codes

The peer API should use conventional HTTP status codes.

| Status | Meaning |
|---|---|
| `200` | Successful request |
| `201` | Resource created |
| `400` | Invalid request |
| `404` | Resource not found |
| `409` | Conflict |
| `413` | Payload too large |
| `422` | Validation failed |
| `500` | Internal error |
| `401` | Missing/invalid signature, stale timestamp, replayed request |
| `403` | Device is unknown or not trusted |
| `408` | Upload stalled / request timeout |
| `415` | Unsupported media type (upload must be `application/zip`) |
| `429` | Too many invalid requests |
| `503` | Service unavailable |
| `507` | Insufficient storage on the receiving device |

Example:

```text
POST /api/v1/transfers
```

successful creation:

```text
201 Created
```

Receive Mode disabled:

```text
409 Conflict
```

File too large:

```text
413 Payload Too Large
```

---

# 35. API Request Correlation

Requests should support a request identifier for debugging.

Example header:

```text
X-Request-ID: req_01JABC123
```

Transfer operations must additionally have a persistent:

```text
transfer_id
```

This allows logs to correlate:

```text
request
  ↓
transfer
  ↓
file operation
  ↓
database operation
```

---

# 36. Idempotency

Transfer creation must avoid accidentally creating duplicate transfer jobs because of repeated requests.

The implementation should support an idempotency mechanism for transfer creation.

Example:

```text
Idempotency-Key: 9d6f8c...
```

If the same request is retried with the same idempotency key, the receiver should return the existing transfer rather than creating a second transfer.

---

# 37. Time Representation

All API timestamps should use ISO 8601 format.

Example:

```text
2026-10-01T10:20:05Z
```

The backend should store timestamps consistently.

The UI may convert timestamps to the user's local timezone for display.

---

# 38. Future Computer Vision API

The CV system is not active in Phase 1.

However, the application reserves a structured event interface for future phases.

The CV system should produce events rather than directly invoking transfer logic.

Conceptually:

```text
CV Engine
   ↓
Gesture Event
   ↓
CV Integration Layer
   ↓
Application State Machine
```

---

# 39. Future CV Event Structure

General structure (ADR-052):

```json
{
  "event_id": "evt_01JABC123",
  "event": "gesture_detected",
  "timestamp": "2026-10-01T10:20:05.123Z",
  "confidence": 0.95,
  "data": {}
}
```

---

# 40. Future CV Events

The following canonical event names are reserved (ADR-052). The earlier uppercase names are superseded.

```text
pointer_move   pointer_click   pointer_down   pointer_up
selection_changed
drag_start     drag_move       drag_end
grab           release
gesture_detected
direction_detected
```

Pointer and drag events carry normalized coordinates (`x`, `y` in 0.0 to 1.0) in `data`:

```json
{ "event": "pointer_move", "confidence": 0.94, "data": { "x": 0.73, "y": 0.42 } }
```

`gesture_detected` carries a gesture state:

```json
{ "event": "gesture_detected", "gesture": "closed_hand", "confidence": 0.94 }
```

`direction_detected` carries a direction:

```json
{ "event": "direction_detected", "direction": "right", "confidence": 0.91 }
```

---

# 41. Future CV Action Mapping

The application core translates CV events into application actions. Example:

```text
gesture_detected (closed_hand)  →  grab selected file
pointer_move                    →  virtual pointer moves
drag_start                      →  file attached to pointer
direction_detected (right)      →  target search begins
selection_changed               →  receiver selected
release                         →  transfer job created
```

The CV engine must not directly invoke:

```text
POST /transfers
```

without passing through the application state machine.

---

# 42. Future CV Integration Endpoint

A future internal endpoint/event channel may be exposed conceptually as:

```text
POST /internal/v1/cv/events
```

This endpoint is **not enabled in Phase 1**.

It exists in the architecture so the CV implementation can be integrated without redesigning the transfer engine.

---

# 43. API Security Boundary

Phase 1 does not implement authentication between explicitly connected peers.

The trust model is:

```text
User discovers device
       ↓
User explicitly clicks Connect
       ↓
Device becomes trusted peer
```

However, the API must still perform validation and reject malformed or invalid requests.

Security details are defined in:

```text
docs/SECURITY.md
```

---

# 43.1 Request Signing

Every peer request (except discovery metadata in mDNS) is signed with the sender's Ed25519 private key (ADR-053).

Headers:

```text
X-Device-ID: <sender device_id>
X-Timestamp: <ISO 8601 UTC>
X-Nonce: <unique random value>
X-Signature: <base64 Ed25519 signature>
```

The signed string is:

```text
METHOD|PATH|device_id|timestamp|nonce|transfer_id
```

(`transfer_id` is empty when the request is not transfer-scoped.)

The receiver rejects the request when:

- the device is not trusted (except for `POST /connection`, where the key in the body verifies the signature);
- the signature is invalid;
- the timestamp is outside a ±60 second window;
- the nonce has already been seen.

Rejections are written to `audit_logs` (`INVALID_DEVICE`).

Every endpoint requires a signature **except** `GET /api/v1/health`, which returns only `{status, device_id}` so reachability can be checked without a handshake.

The signature is verified against the stored public key of a *trusted* device. The single exception is `POST /api/v1/connection`, where the key in the body is used (and must match any stored key).

Repeated invalid requests from one address are rate limited (`429 RATE_LIMITED`) for a short window; only the first crossing of the limit is audited, so a flood cannot fill the audit log.

Path used for signing is the percent-decoded request path. Transfer IDs are limited to `[A-Za-z0-9_-]{1,64}`.

The client verifies the server's identity by pinning its TLS public key.

---

# 44. API Contract Rule

The API is a contract.

Claude Code and future developers must not:

- Rename endpoints arbitrarily.
- Change response structures without updating this document.
- Introduce undocumented transfer states.
- Add authentication without updating `SECURITY.md`.
- Directly couple the UI to peer APIs.
- Allow the CV module to bypass the application core.
- Put binary files into JSON payloads.

Any intentional API-breaking change must be recorded in `DECISIONS.md`.

---

# 45. API Request Flow

Normal file transfer:

```text
                    USER
                     │
                     ▼
                 Tauri UI
                     │
                     │ IPC
                     ▼
              Python Core
                     │
                     │ 1. Create transfer
                     ▼
              POST /transfers
                     │
                     ▼
                Receiver
                     │
                     │ 2. Accept
                     ▼
              Transfer ID
                     │
                     │ 3. Upload archive
                     ▼
          POST /transfers/{id}/data
                     │
                     ▼
                Receiver
                     │
              ┌──────┴──────┐
              │             │
          Validate        Extract
              │             │
              └──────┬──────┘
                     ▼
              Received Storage
                     │
                     ▼
                  SQLite
                     │
                     ▼
                Completed
```

---

# 46. API Design Summary

Phase 1 uses:

```text
Tauri
   │
   │ Local IPC
   ▼
Python Core
   │
   │ HTTP/REST
   ▼
Peer Python Core
   │
   ├── SQLite
   └── File Storage
```

Peer discovery uses:

```text
mDNS
```

File transfer uses:

```text
HTTP streaming
```

Multiple selected files are packaged into:

```text
Temporary ZIP
```

and transferred as one logical transfer job.

The UI obtains transfer progress through:

```text
GET /api/v1/transfers/{transfer_id}
```

The future CV system communicates through a separate structured event interface and remains isolated from the file-transfer implementation.

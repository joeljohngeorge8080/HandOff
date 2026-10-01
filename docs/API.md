# API

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

Used to establish a logical connection with a discovered peer.

### Request

```json
{
  "device_id": "7e7d8c2a-5e9e-4e1c-9a7d-9a1c4e1f7a31",
  "device_name": "Aaron-Laptop",
  "address": "192.168.1.15",
  "port": 8765
}
```

### Response

```json
{
  "connection_id": "conn_01JABC123",
  "device_id": "7e7d8c2a-5e9e-4e1c-9a7d-9a1c4e1f7a31",
  "status": "connected"
}
```

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

# 13. Receive Mode

## GET `/api/v1/receive-mode`

Returns the current receive state.

### Response

```json
{
  "enabled": true
}
```

---

## PUT `/api/v1/receive-mode`

Changes the receive mode.

### Request

```json
{
  "enabled": true
}
```

### Response

```json
{
  "enabled": true
}
```

When:

```json
{
  "enabled": true
}
```

the device automatically accepts valid incoming transfers.

When:

```json
{
  "enabled": false
}
```

incoming transfers must be rejected.

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

## POST `/api/v1/transfers`

Creates a transfer job.

The sender must create the transfer before uploading its data.

### Request

```json
{
  "destination_device_id": "7e7d8c2a-5e9e-4e1c-9a7d-9a1c4e1f7a31",
  "file_count": 3,
  "total_size": 18432000,
  "archive_name": "handoff-transfer-01.zip"
}
```

### Response

```json
{
  "transfer_id": "tr_01JABC789",
  "status": "created",
  "destination_device_id": "7e7d8c2a-5e9e-4e1c-9a7d-9a1c4e1f7a31",
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

Uploads the transfer archive.

The body contains the ZIP archive as a binary stream.

Example:

```http
POST /api/v1/transfers/tr_01JABC789/data
Content-Type: application/zip
Content-Length: 18432000
```

The implementation must stream the request to disk.

It must not load the complete archive into memory.

### Successful response

```json
{
  "transfer_id": "tr_01JABC789",
  "status": "completed",
  "files_received": 3
}
```

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

# 21. Transfer Progress

The Tauri UI should poll:

```text
GET /api/v1/transfers/{transfer_id}
```

rather than requiring WebSockets for Phase 1.

The polling interval should be reasonable and configurable internally.

The UI can use:

```text
bytes_received
total_size
progress
status
```

to render transfer progress.

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
photo (1).jpg
```

If that exists:

```text
photo (2).jpg
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

# 27. Transfer History API

## GET `/api/v1/transfers`

Returns transfer history.

### Example

```json
{
  "items": [
    {
      "transfer_id": "tr_01JABC789",
      "direction": "sent",
      "peer_device_id": "7e7d8c2a-5e9e-4e1c-9a7d-9a1c4e1f7a31",
      "file_count": 3,
      "status": "completed",
      "created_at": "2026-10-01T10:20:00Z",
      "completed_at": "2026-10-01T10:20:05Z"
    }
  ]
}
```

---

# 28. Transfer History Detail

## GET `/api/v1/transfers/{transfer_id}`

The same endpoint provides detailed transfer status.

Once completed, it should contain sufficient information for the UI to display the transfer in history.

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

The Python core then performs the network transfer.

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

## Application

```text
INVALID_REQUEST
INVALID_STATE
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
| `503` | Service unavailable |

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

General structure:

```json
{
  "event_id": "evt_01JABC123",
  "event": "HAND_CLOSED",
  "timestamp": "2026-10-01T10:20:05.123Z",
  "confidence": 0.95,
  "data": {}
}
```

---

# 40. Future CV Events

The following event types are reserved.

## HAND_DETECTED

```json
{
  "event": "HAND_DETECTED",
  "confidence": 0.96,
  "data": {
    "x": 0.62,
    "y": 0.41
  }
}
```

---

## HAND_LOST

```json
{
  "event": "HAND_LOST",
  "confidence": 0.91,
  "data": {}
}
```

---

## HAND_OPENED

```json
{
  "event": "HAND_OPENED",
  "confidence": 0.97,
  "data": {
    "x": 0.62,
    "y": 0.41
  }
}
```

---

## HAND_CLOSED

```json
{
  "event": "HAND_CLOSED",
  "confidence": 0.96,
  "data": {
    "x": 0.62,
    "y": 0.41
  }
}
```

---

## POINTER_MOVE

Used for future hand-controlled mouse movement.

```json
{
  "event": "POINTER_MOVE",
  "confidence": 0.94,
  "data": {
    "x": 0.73,
    "y": 0.42
  }
}
```

Coordinates are normalized:

```text
x = 0.0 → 1.0
y = 0.0 → 1.0
```

---

## POINTER_CLICK

```json
{
  "event": "POINTER_CLICK",
  "confidence": 0.95,
  "data": {
    "button": "left"
  }
}
```

---

## POINTER_DOUBLE_CLICK

```json
{
  "event": "POINTER_DOUBLE_CLICK",
  "confidence": 0.95,
  "data": {
    "button": "left"
  }
}
```

---

## DRAG_START

```json
{
  "event": "DRAG_START",
  "confidence": 0.94,
  "data": {
    "x": 0.61,
    "y": 0.42
  }
}
```

---

## DRAG_MOVE

```json
{
  "event": "DRAG_MOVE",
  "confidence": 0.93,
  "data": {
    "x": 0.74,
    "y": 0.43
  }
}
```

---

## DRAG_END

```json
{
  "event": "DRAG_END",
  "confidence": 0.96,
  "data": {
    "x": 0.82,
    "y": 0.44
  }
}
```

---

## BODY_MOVE_LEFT

```json
{
  "event": "BODY_MOVE_LEFT",
  "confidence": 0.91,
  "data": {
    "movement_score": 0.82
  }
}
```

---

## BODY_MOVE_RIGHT

```json
{
  "event": "BODY_MOVE_RIGHT",
  "confidence": 0.92,
  "data": {
    "movement_score": 0.85
  }
}
```

---

## TARGET_DETECTED

```json
{
  "event": "TARGET_DETECTED",
  "confidence": 0.94,
  "data": {
    "target_device_id": "device_003",
    "target_score": 0.91
  }
}
```

---

## TARGET_LOCKED

```json
{
  "event": "TARGET_LOCKED",
  "confidence": 0.97,
  "data": {
    "target_device_id": "device_003",
    "lock_duration_ms": 3000
  }
}
```

---

# 41. Future CV Action Mapping

The application should eventually translate CV events into application actions.

Example:

```text
HAND_CLOSED
     ↓
Selected file becomes grabbed
```

```text
POINTER_MOVE
     ↓
Virtual pointer moves
```

```text
DRAG_START
     ↓
File becomes attached to pointer
```

```text
BODY_MOVE_RIGHT
     ↓
Target search begins
```

```text
TARGET_LOCKED
     ↓
Receiver becomes selected
```

```text
HAND_OPENED
     ↓
File release
     ↓
Transfer job
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

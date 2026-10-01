# Architecture

## 1. Overview

This project is a desktop application for **gesture-assisted local file transfer between nearby computers**.

The application runs on Windows and Linux and allows two devices on the same Wi-Fi network to connect and transfer files.

Phase 1 is the **MVP** and does not contain computer vision or camera functionality.

The MVP establishes the complete software foundation:

- Desktop application
- Transparent UI
- Local file management
- Gallery/file view
- Device discovery
- Device connection
- Bidirectional communication
- Send/receive functionality
- File transfer
- Transfer history
- Persistent device identity
- Local storage
- Future computer-vision integration boundary

Computer vision will be developed independently and integrated in a later phase.

---

# 2. Architectural Goals

The architecture must:

1. Run as a native desktop application.
2. Support Windows and Linux.
3. Work over the same local Wi-Fi network.
4. Support exactly one connected peer at a time.
5. Support bidirectional file transfer.
6. Transfer multiple files as one transfer job.
7. Support files under 50 MB.
8. Automatically accept incoming files when Receive Mode is enabled.
9. Store imported files in application-managed storage.
10. Maintain transfer history.
11. Maintain a permanent device identity.
12. Keep the networking and file-transfer layer independent from the UI.
13. Allow the future computer-vision module to control the same application through a defined interface.
14. Avoid requiring cloud infrastructure.

---

# 3. High-Level Architecture

The system consists of four major layers:

```text
┌─────────────────────────────────────────────────────────────┐
│                         Tauri UI                            │
│                                                             │
│  Gallery │ Devices │ Send │ Receive │ Transfer Status      │
└───────────────────────────┬─────────────────────────────────┘
                            │
                    Tauri Commands / IPC
                            │
┌───────────────────────────▼─────────────────────────────────┐
│                     Python Application Core                 │
│                                                             │
│  File Manager │ Device Manager │ Transfer Manager           │
│  Session Manager │ History Manager │ Settings Manager       │
└───────────────┬──────────────────────────────┬──────────────┘
                │                              │
                ▼                              ▼
       ┌────────────────┐             ┌──────────────────┐
       │ SQLite Database│             │ Local File Store │
       └────────────────┘             └──────────────────┘
                │
                │
                ▼
┌─────────────────────────────────────────────────────────────┐
│                       Network Layer                         │
│                                                             │
│ LAN Discovery │ Device Connection │ REST API │ File Stream  │
└───────────────────────────┬─────────────────────────────────┘
                            │
                         Wi-Fi LAN
                            │
                            ▼
                  ┌────────────────────┐
                  │ Connected Peer     │
                  │ Same Application   │
                  └────────────────────┘
```

---

# 4. Technology Stack

## Desktop UI

**Tauri**

Responsibilities:

- Native desktop window
- Transparent UI
- Gallery interface
- Device list
- Send/Receive controls
- File selection
- Transfer progress
- Notifications
- Interaction with Python backend

The application is not a web application or hosted website.

Tauri is used as the desktop application shell and UI layer.

---

## Application Backend

**Python**

Responsibilities:

- Application business logic
- File management
- Device management
- Network communication
- Transfer management
- Transfer history
- Local storage management
- Receive mode
- Future computer-vision integration

The Python backend is the authoritative application core.

---

## Database

**SQLite**

SQLite is used for lightweight persistent application metadata.

It stores:

- Device identity
- Connected/known device information
- File metadata
- Transfer history
- Application settings where required

Actual files are not stored inside SQLite.

---

## File Storage

Files are copied into application-managed storage.

Conceptually:

```text
Application Data/
│
├── handoff.db
├── files/
├── received/
├── transfers/
└── temp/
```

The exact platform-specific storage location will be defined during deployment implementation.

---

# 5. Application Components

## 5.1 Tauri UI

The UI provides:

- File/gallery view
- Add files
- File selection
- Connected-device list
- Connect action
- Send button
- Receive Mode toggle
- Transfer status
- Transfer history
- Notifications

The UI does not directly implement file-transfer logic.

It communicates with the Python application core through an internal interface.

---

# 6. Python Application Core

The Python application core is divided into logical services.

```text
Python Core
│
├── File Manager
├── Device Manager
├── Discovery Manager
├── Connection Manager
├── Transfer Manager
├── Receive Manager
├── History Manager
├── Storage Manager
└── CV Integration Layer
```

---

## 6.1 File Manager

Responsible for:

- Importing files
- Validating file types
- Validating file size
- Creating file metadata
- Listing available files
- Deleting application-managed files
- Preparing files for transfer

Phase-1 allowed file types:

```text
.txt
.jpg
.mp4
.exe
```

Maximum file size:

```text
50 MB
```

The file manager must reject unsupported file types and files larger than the Phase-1 limit.

---

# 7. File Import Flow

When the user adds a file:

```text
User
 │
 │ Add File
 ▼
Tauri UI
 │
 ▼
Python File Manager
 │
 ├── Validate extension
 ├── Validate size
 ├── Calculate metadata
 ├── Copy file
 │
 ▼
Application Storage
 │
 ▼
SQLite metadata
 │
 ▼
Gallery updated
```

The original user file is not used as the application's managed copy.

The application creates its own copy.

---

# 8. Gallery Architecture

Phase 1 uses a simple file/gallery interface.

Each file is represented using:

- File icon
- File name
- Selection state

Example:

```text
┌────────────────────────────────────────┐
│ Files                                  │
│                                        │
│  🖼️ photo1.jpg     📝 notes.txt        │
│                                        │
│  🎥 video.mp4      ⚙️ application.exe  │
│                                        │
└────────────────────────────────────────┘
```

Phase 1 supports:

- Mouse selection
- Ctrl multi-selection
- Shift multi-selection
- Drag selection

Multiple selected files can be sent as a single transfer job.

---

# 9. Device Discovery

Devices communicate over the same local Wi-Fi network.

A device advertises that the application is running and available for connection.

The discovery layer identifies:

- Device name
- Permanent device ID
- Local IP address
- Application/service port
- Availability status

Example:

```text
Connected Devices

┌────────────────────────────┐
│ 🟢 Joel-Laptop             │
│    192.168.1.12            │
│                    [Connect]│
├────────────────────────────┤
│ 🟢 Aaron-Laptop            │
│    192.168.1.15            │
│                    [Connect]│
└────────────────────────────┘
```

Discovery is local-network only.

No cloud discovery service is required.

---

# 10. Device Identity

Every installation receives a permanent device identity.

Conceptually:

```text
Device Name:
Joel-Laptop

Device ID:
<unique persistent identifier>
```

The Device ID must remain stable across application restarts and IP-address changes.

The IP address is treated as temporary network information.

The Device ID is the logical identity of the device.

---

# 11. Connection Model

Phase 1 supports exactly **one connected peer at a time**.

Example:

```text
Laptop A
    │
    │ one connection
    ▼
Laptop B
```

The application does not support:

```text
Laptop A
 ├── Laptop B
 ├── Laptop C
 └── Laptop D
```

in Phase 1.

If the user selects another discovered device, the application performs an internal graceful disconnect and then connects to the selected device (ADR-047).

There is no user-facing Disconnect button. An active transfer blocks peer switching until it reaches a terminal state (`completed`, `failed`, `partially_completed`).

---

# 12. Bidirectional Communication

The connection is symmetric.

Either device can send or receive.

```text
Laptop A ◄────────────────► Laptop B
          bidirectional
```

Laptop A may send to Laptop B.

Laptop B may later send to Laptop A.

There is no permanent "server computer" and "client computer" from the user's perspective.

The networking implementation may internally expose server/client endpoints, but the application behavior is peer-to-peer.

---

# 13. Network Protocol

Phase 1 will use:

### HTTP/REST for control operations

Examples:

```text
GET    /api/v1/device
GET    /api/v1/health
POST   /api/v1/connection
DELETE /api/v1/connection
GET    /api/v1/receive-mode
POST   /api/v1/transfers
POST   /api/v1/transfers/{id}/data
GET    /api/v1/transfers/{id}
```

`docs/API.md` is authoritative for endpoint names and payloads.

### HTTP streaming for file transfer

Files are transferred using HTTP request/response streams rather than loading the complete file into memory.

This keeps the implementation simple and prepares the architecture for larger files in future phases.

---

# 14. Why HTTP/REST + Local LAN

HTTP is selected for Phase 1 because:

- It is mature.
- Python has excellent HTTP support.
- Tauri can communicate with the local backend easily.
- Debugging is straightforward.
- File streaming is simple.
- Request/response semantics are appropriate for Phase 1.
- It avoids the complexity of WebRTC.
- It avoids introducing unnecessary persistent WebSocket infrastructure.
- It works naturally on both Windows and Linux.

WebRTC, QUIC and other protocols can be evaluated later if the requirements change.

Phase 1 does not require them.

---

# 15. Device Connection Flow

```text
User opens application
        │
        ▼
Discovery starts
        │
        ▼
Nearby application instances detected
        │
        ▼
User clicks "+"
        │
        ▼
Device list displayed
        │
        ▼
User clicks "Connect"
        │
        ▼
Connection established
        │
        ▼
Peer appears as connected
```

Example:

```text
🟢 Joel-Laptop
Connected
```

If the peer becomes unavailable:

```text
🔴 Joel-Laptop
Offline
```

The UI should update the peer's state immediately when the connection is determined to be unavailable.

---

# 16. Receive Mode

Receive Mode is a toggle.

```text
Receive Mode: OFF
```

or:

```text
Receive Mode: ON
```

When enabled:

> The device is ready to automatically accept incoming files from the connected peer.

When disabled:

> The device should not automatically accept incoming transfers.

Phase 1 does not require an Accept/Reject dialog for every file.

---

# 17. Send Flow

The basic Phase-1 send flow is:

```text
User
 │
 ▼
Select one or more files
 │
 ▼
Send button becomes enabled
 │
 ▼
User clicks Send
 │
 ▼
Check connected device
 │
 ▼
Check receiver readiness
 │
 ▼
Create transfer job
 │
 ▼
Transfer selected files
 │
 ▼
Receiver stores files
 │
 ▼
Transfer history updated
```

If no device is connected:

```text
Send disabled
```

If a device is connected:

```text
Send enabled
```

The UI should still explicitly show the connected destination device before the transfer begins.

---

# 18. Transfer Job

Multiple files are grouped into a single transfer job.

Example:

```text
Transfer #102

Files:
├── photo1.jpg
├── photo2.jpg
├── video.mp4
└── notes.txt

Destination:
Aaron-Laptop
```

The transfer job has one overall status.

Possible statuses:

```text
CREATED
VALIDATING
ACCEPTED
TRANSFERRING
COMPLETED
FAILED
PARTIALLY_COMPLETED
```

There is no `CANCELLED` state in Phase 1 (FR-026).

Individual file progress can be tracked internally.

---

# 19. File Transfer Flow

```text
Sender
 │
 │ Transfer request
 ▼
Receiver
 │
 │ Receive Mode = ON?
 ├─────────────── No → Reject
 │
 ▼
Yes
 │
 ▼
Receive transfer metadata
 │
 ▼
Stream files
 │
 ▼
Validate received files
 │
 ▼
Save to Received/
 │
 ▼
Update database
 │
 ▼
Return success
```

The sender retains its original application-managed files.

The transfer is a **copy operation**, not a move operation.

---

# 20. Received File Storage

Received files are stored separately from imported files.

Conceptually:

```text
Application Storage
│
├── files/
│   └── files added by user
│
└── received/
    └── files received from peers
```

This makes the source of each file clear and simplifies future synchronization logic.

---

# 21. Transfer History

The application maintains transfer history.

Example:

```text
Transfer History

✓ photo.jpg
  Sent → Aaron-Laptop
  09:41

✓ video.mp4
  Received ← Aaron-Laptop
  09:43

✗ program.exe
  Sent → Aaron-Laptop
  Failed
```

History is stored in SQLite.

History must survive application restarts.

---

# 22. Connection Failure

If the connected peer becomes unavailable:

```text
Laptop A ◄──────X──────► Laptop B
```

Laptop A should detect the connection failure and update the UI:

```text
🔴 Laptop B
Offline
```

Pending transfers should transition to an appropriate failure state.

The application must not silently report a successful transfer if the receiver did not confirm completion.

---

# 23. Storage Architecture

Logical storage:

```text
Application Data
│
├── handoff.db
├── files/
├── received/
├── transfers/
└── temp/
```

The exact OS-specific paths will be defined during deployment.

Windows and Linux may use different physical paths while preserving the same logical structure.

---

# 24. Database Responsibility

SQLite stores metadata only.

It should not store the binary contents of images/videos/files.

Conceptually:

```text
SQLite
│
├── device identity
├── known peers
├── file metadata
├── transfer jobs
├── transfer items
└── settings
```

Binary data remains on the filesystem.

---

# 25. Transparent UI

The desktop application uses a transparent/translucent interface.

Phase-1 target:

```text
80% opacity
```

The window should remain usable as a normal desktop application while providing the visual foundation for the future gesture-based interface.

The transparency is a UI characteristic and should not affect the backend architecture.

---

# 26. Computer Vision Integration

Computer vision is intentionally separated from Phase 1.

Future architecture:

```text
             Camera
                │
                ▼
       Computer Vision Module
                │
                ▼
         Gesture Events
                │
                ▼
        CV Integration Layer
                │
                ▼
       Python Application Core
                │
                ▼
        Transfer State Machine
```

The CV system should not directly perform file transfers.

It should report observations/events.

---

# 27. Future Gesture Interface

The eventual interaction uses the canonical event vocabulary of ADR-052:

```text
pointer_move   pointer_click   pointer_down   pointer_up
selection_changed
drag_start     drag_move       drag_end
grab           release
gesture_detected
direction_detected
```

Example future events:

```json
{ "event": "gesture_detected", "gesture": "closed_hand", "confidence": 0.94 }
{ "event": "direction_detected", "direction": "right", "confidence": 0.91 }
```

The application core converts these observations into actions.

Example:

```text
gesture_detected (closed_hand)
     ↓
Grab selected file

direction_detected (right)
     ↓
Target selection

release
     ↓
Transfer
```

This allows the CV system to be developed independently from the transfer system.

---

# 28. Phase Separation

## Phase 1 — MVP

```text
Tauri
  +
Python
  +
SQLite
  +
LAN discovery
  +
HTTP/REST
  +
File transfer
  +
File storage
  +
Transfer history
```

No camera.

No OpenCV.

No gesture recognition.

---

## Future Phase — Computer Vision

Add:

```text
Camera
   ↓
OpenCV / MediaPipe / CV pipeline
   ↓
Hand tracking
   ↓
Gesture detection
   ↓
Body/shoulder tracking
   ↓
Target detection
   ↓
CV Integration Layer
```

without rewriting the underlying transfer engine.

---

# 29. Architectural Principle

The most important architectural rule is:

> **Perception and transfer must remain separate.**

Computer vision answers:

```text
"What is the user doing?"
```

The application core answers:

```text
"What should the software do?"
```

The networking layer answers:

```text
"How should the file reach the other device?"
```

The storage layer answers:

```text
"Where should the file and its metadata be stored?"
```

This separation prevents future computer-vision development from contaminating the core transfer system.

---

# 30. Final Component Diagram

```text
                         USER
                          │
                          ▼
                 ┌────────────────┐
                 │    Tauri UI    │
                 │                │
                 │ Gallery        │
                 │ Devices        │
                 │ Send           │
                 │ Receive        │
                 │ History        │
                 └───────┬────────┘
                         │ IPC
                         ▼
              ┌──────────────────────┐
              │   Python Core        │
              │                      │
              │ File Manager         │
              │ Device Manager       │
              │ Transfer Manager     │
              │ Receive Manager      │
              │ History Manager      │
              │ Storage Manager      │
              │ CV Integration       │
              └───────┬───────┬──────┘
                      │       │
              ┌───────┘       └────────┐
              ▼                        ▼
       ┌──────────────┐        ┌───────────────┐
       │    SQLite    │        │ File Storage  │
       │              │        │               │
       │ Metadata     │        │ Imported      │
       │ History      │        │ Received      │
       │ Devices      │        │               │
       └──────────────┘        └───────────────┘
                      │
                      ▼
              ┌──────────────────┐
              │ Network Layer    │
              │                  │
              │ Discovery        │
              │ HTTP/REST        │
              │ File Streaming   │
              └────────┬─────────┘
                       │
                    Wi-Fi LAN
                       │
                       ▼
              ┌──────────────────┐
              │   Peer Device    │
              │                  │
              │ Tauri + Python   │
              │ SQLite + Storage │
              └──────────────────┘


Future:

Camera
   │
   ▼
Computer Vision
   │
   ▼
CV Integration Layer
   │
   ▼
Python Core
```

---

# 31. Architecture Summary

The Phase-1 MVP is a **native cross-platform desktop application**, not a website.

Its architecture is:

```text
Tauri
   ↓
Python Application Core
   ↓
SQLite + Local Storage
   ↓
LAN Discovery + HTTP/REST
   ↓
Peer Application
```

The system uses a **single peer connection**, supports **bidirectional transfers**, allows **multiple files in one transfer job**, automatically accepts files when **Receive Mode** is enabled, stores files locally, and maintains persistent transfer history.

The architecture deliberately leaves a clean integration boundary for the future computer-vision system.

The computer vision module will eventually provide perception events to the application core, while the existing file-transfer system remains responsible for the actual transfer.

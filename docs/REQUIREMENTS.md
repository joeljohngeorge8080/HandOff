# Requirements

## 1. Document Purpose

This document defines the functional and non-functional requirements for **HandOff Phase 1 (MVP)**.

HandOff is a native desktop application that allows users to manage files and transfer them between connected computers over the same local Wi-Fi network.

Phase 1 focuses on establishing the complete software foundation for file transfer.

Computer vision and camera-based interaction are **not implemented in Phase 1**, but the architecture must provide a clean integration boundary for the future computer-vision system.

---

# 2. Phase 1 Scope

The Phase 1 MVP shall provide:

- Native desktop application
- Windows x64 support
- Linux x64 support
- Transparent 80% opacity UI
- Local file import
- File gallery
- File selection
- Device discovery
- Device connection
- Permanent device identity
- One connected peer at a time
- Bidirectional communication
- Receive Mode
- File transfer
- Multiple-file transfer jobs
- File validation
- Local file storage
- Received file storage
- Duplicate filename handling
- Transfer history
- Transfer notifications
- Connection status
- Future CV integration interface

---

# 3. Explicit Phase 1 Exclusions

The following are intentionally excluded from the MVP:

- Camera access
- OpenCV
- MediaPipe
- Hand detection
- Gesture recognition
- Hand-controlled mouse
- Gesture-controlled file selection
- Gesture-controlled drag and drop
- Spatial file transfer
- Multiple simultaneous peer connections
- Cloud storage
- Internet-based transfer
- Mobile/Android application
- Large-file transfer above 50 MB per file
- Transfer cancellation

These features may be implemented in future phases.

---

# 4. Supported Platforms

### Required

- Windows x64
- Linux x64

### Not required

- Windows ARM
- Linux ARM
- macOS
- Android
- iOS

Phase 1 targets x64 desktop systems only.

---

# 5. Application Type

HandOff shall be a **native desktop software application**.

It is not:

- A website
- A web application
- A cloud service
- A browser-based file-transfer application

The application shall use:

```text
Tauri
+
Python application core
```

---

# 6. File Management Requirements

## FR-001 — Add Files

The user shall be able to add supported files to HandOff.

Supported Phase-1 extensions:

```text
.txt
.jpg
.mp4
.exe
```

File extension matching shall be case-insensitive.

Therefore these shall all be accepted:

```text
photo.jpg
PHOTO.JPG
video.mp4
VIDEO.MP4
notes.txt
NOTES.TXT
program.exe
PROGRAM.EXE
```

---

## FR-002 — File Size Limit

Each individual file shall be smaller than or equal to **50 MB**.

Files exceeding the Phase-1 limit shall be rejected.

The application shall clearly communicate the reason for rejection.

Example:

```text
File rejected.

Maximum supported file size:
50 MB
```

---

## FR-003 — File Copy

When a file is added, HandOff shall create its own copy inside application-managed storage.

The original file shall remain untouched.

```text
Original File
     │
     ├────────── remains unchanged
     │
     ▼
HandOff Storage
     │
     ▼
Gallery
```

---

## FR-004 — File Metadata

HandOff shall maintain metadata for application-managed files.

Metadata may include:

- File ID
- File name
- Extension
- Size
- Storage path
- Import timestamp
- File type
- Source type

The binary file itself shall not be stored inside SQLite.

---

# 7. Gallery Requirements

## FR-005 — File Display

Imported files shall appear inside the HandOff gallery.

Each item shall display:

- File icon
- File name

Example:

```text
┌─────────────────────────────────┐
│                                 │
│  🖼️ photo.jpg    🎥 video.mp4   │
│                                 │
│  📝 notes.txt    ⚙️ program.exe  │
│                                 │
└─────────────────────────────────┘
```

---

## FR-006 — File Selection

The user shall be able to select files using a mouse.

Phase 1 shall support:

- Single selection
- Ctrl-based multi-selection
- Shift-based multi-selection
- Drag selection

---

## FR-007 — Multiple Files

The user shall be able to select multiple files and transfer them as a single transfer job.

Example:

```text
photo.jpg
video.mp4
notes.txt
```

shall be transferable as:

```text
Transfer Job #123
├── photo.jpg
├── video.mp4
└── notes.txt
```

---

# 8. Drag-and-Drop Import

Direct file dragging from the operating system file manager into HandOff is **not part of the Phase-1 MVP**.

This capability is reserved for a later phase.

Phase 1 uses the application's file-add mechanism.

---

# 9. File Deletion

## FR-008 — Delete Managed Files

The user shall be able to delete files from the HandOff gallery.

Deleting a file from HandOff shall delete the application's managed copy.

It shall **not delete the original source file** from the user's operating-system filesystem.

Example:

```text
Original:
~/Pictures/photo.jpg

HandOff:
ApplicationData/files/imported/photo.jpg
```

Deleting the HandOff copy shall not affect:

```text
~/Pictures/photo.jpg
```

---

# 10. Device Discovery

## FR-009 — Local Device Discovery

HandOff shall discover other HandOff installations available on the same local Wi-Fi network.

The discovered device information shall include:

- Device name
- Device ID
- IP address
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

---

# 11. Device Identity

## FR-010 — Permanent Device ID

Each HandOff installation shall have a unique persistent Device ID.

The Device ID shall survive:

- Application restart
- IP address changes
- Network changes
- Application configuration changes

The IP address shall not be treated as the permanent identity of a device.

---

# 12. Device Connection

## FR-011 — Connect

The user shall be able to connect to a discovered device by clicking:

```text
[Connect]
```

The connection shall be established without requiring manual approval on the other device.

---

## FR-012 — Connection Confirmation

After successful connection, the initiating device shall display a success indication.

Example:

```text
✓ Connected to Aaron-Laptop
```

---

## FR-013 — One Peer Only

Phase 1 shall support exactly one connected peer at a time.

Example:

```text
Laptop A
   │
   ▼
Laptop B
```

The application shall not support:

```text
Laptop A
 ├── Laptop B
 ├── Laptop C
 └── Laptop D
```

during Phase 1.

The user may switch to another discovered device. Switching releases the current connection internally and is blocked while a transfer is active (ADR-046, ADR-047).

---

## FR-014 — Bidirectional Connection

A connection shall be bidirectional.

Both connected devices may:

- Send files
- Receive files

There is no permanent sender or receiver device.

---

# 13. Connection Availability

## FR-015 — Offline Detection

If a connected device becomes unavailable, HandOff shall update its status to offline immediately after the connection failure is detected.

Example:

```text
🔴 Aaron-Laptop
Offline
```

The application shall not continue to display a disconnected device as available.

---

## FR-016 — No Disconnect Button

Phase 1 does not require a user-facing Disconnect button.

Connection lifecycle management is handled by the application.

---

# 14. Receive Mode

## FR-017 — Receive Mode Toggle

The application shall provide a Receive Mode toggle.

```text
Receive Mode: OFF
```

or:

```text
Receive Mode: ON
```

---

## FR-018 — Receive Mode Independent of Connection

The user shall be able to enable Receive Mode even when no peer is currently connected.

Example:

```text
Receive Mode: ON

No device connected.
Waiting for connection...
```

---

## FR-019 — Automatic Receiving

When Receive Mode is enabled and the connected peer sends a file, HandOff shall automatically accept the incoming transfer.

No per-file Accept/Reject dialog is required in Phase 1.

---

## FR-020 — Receive Mode Disabled

If Receive Mode is disabled, an incoming transfer shall not be accepted.

The sender shall receive an appropriate response such as:

```text
Laptop B is not accepting files.
```

---

# 15. Send Requirements

## FR-021 — Send Button State

The Send button shall only become enabled when:

```text
At least one file is selected
        AND
A peer is connected
```

Otherwise:

```text
Send = Disabled
```

---

## FR-022 — Send Flow

The basic send operation shall be:

```text
Select file(s)
      ↓
Connected device exists
      ↓
Send enabled
      ↓
User clicks Send
      ↓
Transfer job created
      ↓
Receiver readiness checked
      ↓
Transfer begins
```

---

## FR-023 — Receiver Readiness

Before transferring, the sender shall determine whether the connected device is accepting files.

If Receive Mode is OFF:

```text
Transfer blocked
```

The sender shall display an appropriate error/status message.

---

# 16. Transfer Requirements

## FR-024 — Transfer Job

Every send operation shall create a transfer job.

A transfer job may contain one or more files.

Example:

```text
Transfer Job #42

Destination:
Aaron-Laptop

Files:
├── photo.jpg
├── video.mp4
└── notes.txt
```

---

## FR-025 — Copy Semantics

Transfers shall use **copy semantics**.

The source files shall remain available on the sender.

Example:

```text
Sender
photo.jpg
   │
   ├───────────────► Receiver
   │
   └── remains on sender
```

---

## FR-026 — No Transfer Cancellation

Phase 1 shall not provide transfer cancellation.

Once a transfer begins, the application attempts to complete the transfer.

---

## FR-027 — Transfer Status

The UI shall provide clear transfer status.

Minimum states:

```text
Pending
Transferring
Completed
Failed
```

A transfer containing multiple files may be marked:

```text
Partially Completed
```

when some files succeed and others fail.

---

# 17. Duplicate Filename Handling

## FR-028 — Automatic Rename

If a received file has the same name as an existing file, HandOff shall automatically generate a unique filename.

Example:

```text
photo.jpg
photo(1).jpg
photo(2).jpg
photo(3).jpg
```

The existing file shall never be overwritten automatically.

---

# 18. Duplicate Content

## FR-029 — Duplicate File Copy

If the exact same file already exists on the receiving device, HandOff shall still copy the file.

Phase 1 does not perform content-based deduplication.

---

# 19. Received File Storage

## FR-030 — Received Directory

Received files shall be stored in the application's dedicated Received storage.

Conceptually:

```text
HandOff Storage
│
├── imported/
│
└── received/
```

The received file shall also appear in the application's file/gallery system as appropriate.

---

# 20. Transfer History

## FR-031 — Transfer History

HandOff shall maintain transfer history.

The history shall contain sufficient information to determine:

- Transfer ID
- Direction
- Peer device
- Files
- Timestamp
- Status
- Failure information where applicable

Example:

```text
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

---

## FR-032 — Configurable History Retention

Transfer history retention shall be user configurable.

The application shall not impose a single fixed retention period.

The exact configuration mechanism may be implemented as part of the settings system.

---

# 21. Notifications

## FR-033 — Transfer Notifications

The application shall provide desktop notifications for significant transfer events.

Examples:

```text
HandOff

3 files received from Aaron-Laptop.
```

```text
HandOff

Transfer completed successfully.
```

```text
HandOff

Transfer failed.
```

---

# 22. User Interface Requirements

## FR-034 — Transparent Window

The main application window shall use approximately **80% opacity**.

The interface should appear translucent while remaining readable and usable.

---

## FR-035 — Device List

The UI shall provide a connection-device list.

Users shall be able to:

- View available devices
- See device status
- Connect to a device

---

## FR-036 — Send Control

The UI shall contain a Send control.

Its enabled state shall reflect whether:

```text
File selected
+
Connected peer
```

are both available.

---

## FR-037 — Receive Control

The UI shall contain a Receive Mode toggle.

Example:

```text
Receive
[ OFF ]

Receive
[ ON ]
```

---

# 23. Network Requirements

## NFR-001 — Local Network

Phase 1 communication shall operate over the same local Wi-Fi network.

Internet connectivity shall not be required for normal file transfer.

---

## NFR-002 — No Cloud Dependency

The MVP shall not require:

- Cloud storage
- Cloud authentication
- Cloud servers
- External APIs
- Internet-based relay servers

for normal operation.

---

## NFR-003 — Protocol

The Phase-1 networking implementation shall use:

```text
HTTP/REST
+
HTTP file streaming
```

for device communication and file transfer.

---

# 24. Performance Requirements

## NFR-004 — File Limit

Each individual file shall be limited to:

```text
≤ 50 MB
```

---

## NFR-005 — Memory Usage

The application should stream files rather than loading complete files into memory during transfer.

This requirement prevents unnecessary memory consumption.

---

## NFR-006 — UI Responsiveness

File transfers shall not block the UI thread.

The user shall be able to continue interacting with the application while a transfer is running.

---

# 25. Reliability Requirements

## NFR-007 — Transfer Integrity

The receiver shall only mark a file as successfully transferred after the complete file has been received and validated.

---

## NFR-008 — Accurate Status

The application shall not report a transfer as successful if the receiver did not successfully complete the transfer.

---

## NFR-009 — Partial Failure

For multi-file transfer jobs, individual file failures shall be tracked.

Example:

```text
photo.jpg     ✓
video.mp4     ✓
program.exe   ✗

Overall:
PARTIALLY_COMPLETED
```

---

# 26. Security Requirements

Detailed security requirements are defined separately in:

```text
docs/SECURITY.md
```

Phase 1 assumes that devices intentionally connected by the user become trusted peers.

The application shall still validate:

- File type
- File size
- Transfer metadata
- Transfer state
- Peer communication state

Security shall not depend solely on the fact that both devices are on the same Wi-Fi network.

---

# 27. Persistence Requirements

The following information shall survive application restart:

- Device identity
- Imported file metadata
- Application-managed files
- Received files
- Transfer history
- Relevant user settings

SQLite shall be used for persistent metadata.

---

# 28. Computer Vision Integration Requirements

## FR-038 — CV Integration Boundary

The Phase-1 application shall contain an explicit integration boundary for the future computer-vision module.

The CV system shall eventually be capable of providing structured events to the application.

Example:

```json
{
  "event": "gesture_detected",
  "gesture": "closed_hand",
  "confidence": 0.94
}
```

---

## FR-039 — CV Independence

The computer-vision implementation shall remain independent from:

- File storage
- Database
- Network transfer
- Transfer protocol
- Peer connection logic

The CV module shall report perception/gesture information.

The application core shall determine what action should occur.

---

## FR-040 — Future Gesture Control

The architecture shall allow future phases to replace mouse-driven interactions with:

- Hand-controlled pointer
- Hand selection
- Hand click
- Hand drag
- Hand grab
- Hand release
- Spatial target selection

without requiring a rewrite of the underlying file-transfer engine.

---

# 29. Future Phase 2 Direction

Phase 2 is expected to introduce the computer-vision interaction model.

Conceptually:

```text
Camera
   ↓
Hand Detection
   ↓
Hand Position
   ↓
Virtual Mouse Pointer
   ↓
Select / Click / Drag
   ↓
Grab File
   ↓
Move Toward Another Device
   ↓
Release
   ↓
Transfer
```

The Phase-1 mouse-based file selection and transfer system must therefore remain reusable.

---

# 30. Acceptance Criteria

Phase 1 is considered successful when all of the following are true.

### Application

- [ ] HandOff runs as a desktop application.
- [ ] Windows x64 is supported.
- [ ] Linux x64 is supported.
- [ ] Main window has approximately 80% opacity.

### Files

- [ ] User can add `.txt` files.
- [ ] User can add `.jpg` files.
- [ ] User can add `.mp4` files.
- [ ] User can add `.exe` files.
- [ ] Unsupported extensions are rejected.
- [ ] Files over 50 MB are rejected.
- [ ] Added files are copied into HandOff storage.
- [ ] Original files remain untouched.
- [ ] Files appear in the gallery.
- [ ] User can select multiple files.
- [ ] User can delete HandOff-managed files.

### Devices

- [ ] Other HandOff devices on the same Wi-Fi can be discovered.
- [ ] Devices have persistent identities.
- [ ] User can connect to a device.
- [ ] Successful connection is shown in the UI.
- [ ] Only one peer can be connected at a time.
- [ ] Connection is bidirectional.
- [ ] Offline peers are detected.

### Sending

- [ ] Send remains disabled without a selected file.
- [ ] Send remains disabled without a connected device.
- [ ] Send becomes enabled when both conditions are satisfied.
- [ ] Multiple files can be sent as one transfer job.
- [ ] Source files remain on the sender.

### Receiving

- [ ] Receive Mode can be enabled.
- [ ] Receive Mode can be enabled without a connection.
- [ ] Incoming files are automatically accepted when Receive Mode is ON.
- [ ] Incoming files are rejected when Receive Mode is OFF.
- [ ] Received files are saved in the Received directory.

### File Conflicts

- [ ] Existing filenames are never overwritten automatically.
- [ ] Duplicate names generate `(1)`, `(2)`, etc.
- [ ] Duplicate content is still copied.

### History

- [ ] Completed transfers appear in history.
- [ ] Failed transfers appear in history.
- [ ] Partially completed jobs are represented correctly.
- [ ] History survives application restart.
- [ ] History retention is configurable.

### Notifications

- [ ] Successful transfers generate notifications.
- [ ] Received transfers generate notifications.
- [ ] Failed transfers generate notifications.

### CV Integration

- [ ] CV integration boundary exists.
- [ ] CV events can theoretically be passed into the application core.
- [ ] No CV implementation is required for MVP.
- [ ] File transfer does not depend on OpenCV.

---

# 31. Phase 1 Definition of Done

The MVP is considered complete when a user can perform the following complete workflow:

```text
Install HandOff
      ↓
Launch HandOff
      ↓
Receive persistent Device ID
      ↓
Discover another HandOff device
      ↓
Connect
      ↓
Add files
      ↓
Files appear in gallery
      ↓
Select one or more files
      ↓
Enable Receive Mode on destination
      ↓
Click Send
      ↓
Files transfer over local Wi-Fi
      ↓
Destination automatically accepts
      ↓
Files appear in Received storage
      ↓
Both devices display transfer status
      ↓
Desktop notification appears
      ↓
Transfer appears in history
```

If this workflow works reliably, Phase 1 is complete.

---

# 32. Phase 1 Non-Goals

The following shall not be used to expand MVP scope:

> "It would be cool if..."

Any feature involving:

- Cameras
- AI
- Gesture recognition
- Spatial awareness
- Multiple receivers
- Internet transfer
- Mobile devices
- Cloud synchronization
- Large files
- Multi-user accounts

belongs to a future phase unless explicitly promoted into the Phase-1 requirements.

The MVP should remain focused on proving the **core peer-to-peer file transfer engine** before introducing computer vision.

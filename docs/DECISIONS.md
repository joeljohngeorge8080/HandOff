# Architecture Decision Records

## 1. Purpose

This document records important architectural and engineering decisions made for HandOff.

The purpose is to prevent future development from unintentionally changing the project's architecture, scope, or core behavior.

Decisions should be changed only when there is a clear technical reason.

---

# ADR-001: Desktop Application Architecture

**Status:** Accepted

**Decision:** HandOff will be developed as a desktop application using Tauri with a Python backend.

```text
Tauri
  │
  ├── Frontend / UI
  │
  └── Python Backend
          │
          ├── Device Management
          ├── File Management
          ├── Transfer Engine
          ├── Database
          └── Network Communication
```

### Rationale

Tauri provides a lightweight desktop shell while Python is appropriate for:

- Networking
- Filesystem operations
- Computer vision integration in future phases
- Transfer logic
- Cryptography
- Backend development

### Rejected Alternatives

**Electron**

Rejected because it generally has a larger runtime footprint.

**Web application**

Rejected because HandOff requires direct filesystem, device, LAN, and future camera/computer-vision integration.

---

# ADR-002: Peer-to-Peer Architecture

**Status:** Accepted

**Decision:** HandOff will use direct device-to-device communication over the local network.

```text
Laptop A ←──── LAN ────→ Laptop B
```

There will be no central HandOff server in Phase 1.

### Rationale

The application is fundamentally a nearby-device transfer system.

A central server would add:

- Infrastructure
- Latency
- Cost
- Availability dependencies
- Unnecessary complexity

---

# ADR-003: LAN-Only Communication

**Status:** Accepted

**Decision:** Phase 1 transfers occur over the same local Wi-Fi/LAN.

### Rationale

The initial use case assumes physically nearby devices.

LAN communication provides:

- Low latency
- High transfer speed
- No Internet dependency
- Simple deployment

Internet/WAN transfer is outside Phase-1 scope.

---

# ADR-004: Windows x64 and Linux x64

**Status:** Accepted

**Decision:** Phase 1 supports:

```text
Windows x64
Linux x64
```

### Rationale

The development environment includes Linux while Windows is a primary target.

Supporting ARM and additional platforms would increase MVP complexity without providing enough immediate value.

---

# ADR-005: Tauri Packaging

**Status:** Accepted

**Decision:** Tauri will package the complete application.

The user should install one application rather than manually installing and configuring the Python backend.

### Rationale

The user experience should be:

```text
Download
   ↓
Install
   ↓
Launch HandOff
```

not:

```text
Install Python
Install dependencies
Create environment
Start backend
Start frontend
```

---

# ADR-006: Bundled Python Runtime

**Status:** Accepted

**Decision:** The Python runtime and required Python dependencies will be bundled with production builds.

### Rationale

HandOff should not require Python to already exist on the user's computer.

This also makes deployments more predictable.

### Consequence

The build pipeline becomes more complex because the Python runtime must be packaged correctly for each platform.

This complexity is accepted.

---

# ADR-007: SQLite Database

**Status:** Accepted

**Decision:** HandOff will use SQLite for Phase 1 persistent application data.

### Rationale

HandOff is a local desktop application.

SQLite provides:

- Zero database server
- Local persistence
- Simple deployment
- Transaction support
- Good reliability
- Easy backup
- Low resource usage

A remote PostgreSQL/MySQL server would be unnecessary for the MVP.

---

# ADR-008: Persistent Device Identity

**Status:** Accepted

**Decision:** Every HandOff installation receives a permanent local device identity.

The identity survives application restarts.

### Rationale

Other HandOff devices need to recognize the same machine across sessions.

A device identity must not change every time HandOff starts.

---

# ADR-009: Trusted LAN Model

**Status:** Accepted

**Decision:** Phase 1 uses a trusted-LAN security model.

Devices explicitly connected by the user become trusted devices.

### Rationale

The initial environment is a controlled local network.

This keeps Phase 1 connection UX simple while still preventing arbitrary unknown devices from transferring files.

### Future Consideration

A stronger authentication and pairing mechanism may be introduced later.

---

# ADR-010: No User Account System

**Status:** Accepted

**Decision:** HandOff will not require user accounts or cloud authentication.

### Rationale

The application identifies devices rather than users.

Adding:

```text
Email
Password
OAuth
Cloud accounts
```

would introduce unnecessary infrastructure and complexity.

---

# ADR-011: Bidirectional Devices

**Status:** Partly superseded by ADR-054 / ADR-055 (Receive Mode removed)

**Decision:** Every HandOff installation can both send and receive files.

The application provides:

```text
Send
Receive Mode
```

### Rationale

A laptop should not need separate sender/receiver software.

Both devices run the same application.

---

# ADR-012: Receive Mode as a Toggle

**Status:** Superseded by ADR-055

**Decision:** Receive Mode is controlled through a toggle.

```text
OFF
ON
```

When enabled, the device is ready to accept transfers from trusted connected devices.

### Rationale

This provides an explicit user-controlled receiving state without requiring a permanent acceptance prompt for every transfer.

---

# ADR-013: Automatic Receiving

**Status:** Amended by ADR-055 (no Receive Mode gate; destination is receiver-chosen)

**Decision:** When Receive Mode is enabled and a trusted connected device sends a valid transfer, the receiving device automatically accepts it.

### Rationale

The intended interaction is fast nearby file transfer.

Repeated confirmation dialogs would undermine the interaction model.

Security is handled through trusted-device relationships and transfer validation.

---

# ADR-014: Explicit Device Selection

**Status:** Accepted

**Decision:** The sender must select the destination device before sending.

If multiple devices are connected, the user selects exactly one.

### Rationale

The same LAN may contain multiple HandOff devices.

Implicitly selecting a destination could result in accidental transfers.

---

# ADR-015: One Active Transfer

**Status:** Accepted

**Decision:** Phase 1 permits only one active transfer job at a time.

### Rationale

This significantly simplifies:

- Transfer state management
- UI
- Resource usage
- Failure recovery
- Testing

Parallel transfer scheduling can be added later if required.

---

# ADR-016: Multiple Files as One Transfer Job

**Status:** Accepted

**Decision:** Multiple selected files are grouped into a single transfer job.

Conceptually:

```text
Files:
    photo.jpg
    video.mp4
    notes.txt

        ↓

Transfer Job
        ↓

Package
        ↓

Transfer
```

### Rationale

This gives the user a single transfer operation and simplifies progress/history management.

---

# ADR-017: File Size Limit

**Status:** Accepted

**Decision:** Phase 1 restricts individual files to a maximum of 50 MB.

### Rationale

Phase 1 is an MVP.

Large-file optimization, streaming architecture, resumable transfers, and advanced congestion handling are deferred.

---

# ADR-018: Supported File Types

**Status:** Superseded by ADR-055 (new type list)

**Decision:** Phase 1 supports:

```text
.txt
.jpg
.mp4
.exe
```

### Rationale

The project intentionally begins with a restricted file set.

Additional formats can be introduced after the transfer pipeline is stable.

---

# ADR-019: SHA-256 Integrity Verification

**Status:** Accepted

**Decision:** File integrity will be verified using SHA-256.

### Rationale

A cryptographic hash provides a deterministic mechanism for detecting corruption or modification during transfer.

The receiver must not report successful completion when integrity verification fails.

---

# ADR-020: Duplicate Filename Handling

**Status:** Accepted

**Decision:** Existing destination files must never be silently overwritten.

Example:

```text
photo.jpg
photo(1).jpg
photo(2).jpg
```

### Rationale

Silent overwriting could destroy user data.

Automatic collision naming is safer and predictable.

---

# ADR-021: Preserve Original Filenames

**Status:** Accepted

**Decision:** Original filenames are preserved whenever possible.

### Rationale

Users need to identify their transferred files naturally.

Collision handling is responsible for creating a safe alternative when necessary.

---

# ADR-022: Separate Application Storage

**Status:** Amended by ADR-055 (received files go to the receiver-chosen folder)

**Decision:** HandOff maintains its own application-managed storage.

Conceptually:

```text
HandOff Storage
├── Imported
├── Received
├── Transfers
└── Temporary
```

### Rationale

HandOff should not manipulate arbitrary user filesystem locations beyond explicit file selection.

---

# ADR-023: Logical File Deletion

**Status:** Amended by ADR-054 (no gallery; dropped copies are deleted after transfer)

**Decision:** Deleting a file from the HandOff gallery does not immediately destroy historical records.

The active file record/storage can be removed or marked inactive while:

```text
History
Audit logs
Transfer records
```

remain available.

### Rationale

The user explicitly requires persistent transfer history and auditability.

---

# ADR-024: Audit Logs

**Status:** Accepted

**Decision:** HandOff will maintain a dedicated `audit_logs` database structure.

### Rationale

Important events need a persistent record independent of the current gallery state.

Examples:

```text
Device connected
Device offline
File imported
Transfer started
Transfer completed
Transfer failed
Security rejection
Hash mismatch
```

---

# ADR-025: No Permanent Deletion of Audit History

**Status:** Accepted

**Decision:** Audit records are not permanently deleted through normal application operations.

### Rationale

Audit history must remain useful even when files or transfer records are no longer active.

---

# ADR-026: No Antivirus Scanning

**Status:** Accepted

**Decision:** HandOff will not perform antivirus scanning in Phase 1.

### Rationale

Antivirus functionality is outside the core purpose of HandOff.

The application will validate:

- File type
- File size
- Path safety
- Device trust
- Transfer integrity

but will not attempt to determine whether a file is malicious.

---

# ADR-027: No Custom Firewall Management

**Status:** Accepted

**Decision:** HandOff will not manage or modify the operating system firewall.

### Rationale

Firewall configuration is an OS responsibility.

Adding firewall-management logic would create unnecessary platform-specific complexity.

---

# ADR-028: No Automatic Updates

**Status:** Accepted

**Decision:** Phase 1 uses manual application updates.

### Rationale

An update service is unnecessary for the MVP and would introduce additional infrastructure and security requirements.

---

# ADR-029: Stable Release Channel Only

**Status:** Accepted

**Decision:** Phase 1 uses one release channel:

```text
Stable
```

### Rationale

Beta/nightly distribution infrastructure is unnecessary during the initial development stage.

---

# ADR-030: Unsigned MVP Builds

**Status:** Accepted

**Decision:** Phase 1 release binaries will be unsigned.

### Rationale

Code signing adds cost and administrative complexity.

Signing can be introduced before public distribution.

---

# ADR-031: Windows Installer + Linux AppImage

**Status:** Accepted

**Decision:** Phase 1 deployment formats are:

```text
Windows → Installer
Linux   → AppImage
```

`.deb` packaging is deferred.

### Rationale

These formats provide sufficient coverage for the MVP without multiplying packaging work.

---

# ADR-032: No Cloud Deployment

**Status:** Accepted

**Decision:** There is no AWS, VPS, database server, or cloud backend in Phase 1.

### Rationale

HandOff is fundamentally a local peer-to-peer application.

Cloud infrastructure would add complexity without contributing to the MVP's core objective.

---

# ADR-033: Computer Vision Deferred to Phase 2

**Status:** Amended by ADR-054 (computer vision is now Phase 3)

**Decision:** Phase 1 does not depend on computer vision.

Computer vision functionality is developed independently and integrated in Phase 2.

### Phase 2 Vision

The planned interaction includes:

```text
Camera
   ↓
Hand detection
   ↓
Gesture recognition
   ↓
Mouse pointer interaction
   ↓
Grab/select
   ↓
Directional movement
   ↓
Transfer
```

### Rationale

Separating CV development from the transfer infrastructure allows both teams to work in parallel.

---

# ADR-034: Camera Activation Model

**Status:** Accepted

**Decision:** Camera access is not required for Phase 1.

In the future CV mode, camera activation will be tied to the relevant interaction mode rather than continuously running.

### Rationale

Continuous camera access is unnecessary when the feature is not being used.

This also reduces:

- Resource usage
- Privacy concerns
- Unnecessary background processing

---

# ADR-035: Phase-Based Development

**Status:** Amended by ADR-054 (Phase 2 is the edge UX; computer vision is Phase 3)

**Decision:** HandOff will be developed incrementally.

### Phase 1

```text
Desktop UI
+
Device connections
+
File gallery
+
Send
+
Receive
+
LAN transfer
+
History
+
Database
```

### Phase 2

```text
Computer Vision
+
Hand detection
+
Gesture recognition
+
Mouse pointer
+
Grab/drop interaction
```

### Future

```text
Mobile
+
Additional interaction mechanisms
+
Advanced transfer capabilities
```

### Rationale

The transfer infrastructure must be stable before complex CV interaction is introduced.

---

# ADR-036: Same Software on Every Device

**Status:** Accepted

**Decision:** Every participating device runs the same HandOff application.

There is no:

```text
Sender App
Receiver App
```

split.

### Rationale

A peer can send or receive depending on its current state.

---

# ADR-037: No Disconnect Button in Phase 1

**Status:** Accepted

**Decision:** The UI will not expose a manual disconnect button in Phase 1.

### Rationale

The initial connection model is intentionally simple.

Devices are trusted and available while reachable.

More sophisticated connection management can be added later.

---

# ADR-038: User-Configurable Settings

**Status:** Accepted

**Decision:** User-configurable settings will be stored locally.

Settings must survive application restarts.

### Rationale

Persistent configuration is required for a usable desktop application.

---

# ADR-039: API Designed for Future CV Integration

**Status:** Accepted

**Decision:** The backend API should reserve and define interfaces for future computer-vision actions even though CV is not implemented in Phase 1.

Potential future actions include:

```text
pointer_move
pointer_click
pointer_down
pointer_up
drag_start
drag_move
drag_end
grab
release
gesture_detected
selection_changed
direction_detected
```

### Rationale

The Phase-1 architecture should not block Phase-2 computer-vision integration.

However, unused CV functionality must not unnecessarily complicate the Phase-1 implementation.

---

# ADR-040: No Authentication System

**Status:** Accepted

**Decision:** HandOff will not implement user authentication in Phase 1.

### Rationale

Device trust replaces account-based authentication for the initial LAN environment.

---

# ADR-041: Security Model

**Status:** Accepted

**Decision:** The Phase-1 security model is:

```text
Trusted LAN
+
Trusted Devices
+
Encrypted Communication
+
File Validation
+
SHA-256 Integrity
+
Filesystem Isolation
+
Audit Logging
```

### Rationale

This provides a practical security baseline without introducing a centralized identity provider.

---

# ADR-042: No Silent Overwrite

**Status:** Accepted

**Decision:** HandOff must never silently overwrite existing destination files.

### Rationale

File transfer software must prioritize preservation of user data over convenience.

---

# ADR-043: Application Data Removal on Uninstall

**Status:** Accepted

**Decision:** Uninstalling HandOff removes HandOff-managed application data.

### Rationale

The application should not leave behind potentially large amounts of transferred files, history, and database data after removal.

The uninstall process must not touch unrelated user data.

---

# ADR-044: CI/CD Deferred

**Status:** Accepted

**Decision:** Automated GitHub Actions release builds are planned but not required for the first MVP implementation.

Future workflow:

```text
Git tag
  ↓
GitHub Actions
  ↓
Windows build
Linux build
  ↓
Release artifacts
```

### Rationale

The project should first establish a stable build process before automating releases.

---

# ADR-045: Decision Change Policy

**Status:** Accepted

Future changes to accepted decisions must:

1. Identify the existing ADR.
2. Explain why it is no longer appropriate.
3. Describe the replacement decision.
4. Document technical consequences.
5. Update affected documentation.
6. Update implementation where required.

Do not silently change architecture.

---

# ADR-046: Single Connected Peer in Phase 1

**Status:** Accepted

Phase 1 supports exactly **one connected peer at a time**.

The application may discover multiple HandOff devices on the LAN, but only one device may be actively connected as the transfer peer.

```text
Discovered:
    Laptop A
    Laptop B
    Laptop C

Active connection:
    Laptop B
```

The user may switch to another discovered device, but the current connection must be released first.

### Rationale

Multi-peer connection management is unnecessary for the MVP and complicates connection state, device selection, transfer routing, UI, testing, and failure handling.

### Non-Goal

Claude Code must **not implement simultaneous connections to multiple peers in Phase 1**.

If a future requirement needs multi-peer support, it is an architectural change: update the relevant documentation before implementation.

---

# ADR-047: Switching Connected Peers

**Status:** Accepted

Phase 1 does not expose a manual **Disconnect** button.

When the user selects another discovered device while already connected:

```text
Current connection
        ↓
Graceful disconnect
        ↓
Connect to selected device
        ↓
New peer becomes active
```

The disconnect operation is an internal connection-management operation, not a user-facing UI action.

### Rules

- Only one peer can be active.
- Switching peers automatically releases the current connection.
- The user does not need to manually disconnect first.
- An active transfer must prevent peer switching until the transfer reaches a terminal state.

Terminal states: `completed`, `failed`, `partially_completed`.

If an active transfer exists, the switch must be rejected rather than silently interrupting the transfer.

---

# ADR-048: Trusted Device Database Model

**Status:** Accepted

Every known device must contain enough persistent information to identify and trust it.

The `devices` table must contain at minimum: `id`, `device_name`, device identity, IP address, `public_key`, `is_trusted`, `status`, `created_at`, `updated_at`, `last_seen_at`.

Exact column names follow `DATABASE.md` (`device_id`, `last_ip`, ...).

- `public_key` stores the public cryptographic identity of the device. The corresponding private key is never stored in the database and never leaves the local device.
- `is_trusted` is true when the local user has trusted the device, meaning it may participate in transfers.

### Rationale

The security model depends on persistent device identity and trust, so the database must represent both.

---

# ADR-049: File Hash Is Stored Before Transfer

**Status:** Accepted

Every managed file must have a SHA-256 hash calculated when it enters HandOff's managed storage. The file record contains `sha256`, and the transfer system uses it as the expected integrity value.

```text
User selects/imports file
        ↓
Calculate SHA-256
        ↓
Store file metadata + hash
        ↓
Create transfer
        ↓
Send manifest including expected hash
        ↓
Receiver calculates SHA-256
        ↓
Compare hashes
        ↓
Success / failure
```

### Rationale

The hash should not need to be calculated for the first time during transfer validation.

---

# ADR-050: Transfer API Hash Contract

**Status:** Accepted

The frontend and the internal `transfer.create` action accept **file IDs only**, never client-supplied hashes.

```json
{
  "destination_device_id": "device-123",
  "file_ids": ["file-001", "file-002"]
}
```

The backend retrieves the stored `file_id`, filename, size and `sha256` and creates the transfer from the trusted stored values.

### Rationale

Allowing the frontend to submit arbitrary hashes would create an unnecessary trust boundary. The backend is authoritative for file metadata.

---

# ADR-051: Transfer Metadata Contains Integrity Information

**Status:** Accepted

When a transfer is prepared, the backend creates a transfer **manifest** containing the expected SHA-256 hash for every file.

```json
{
  "transfer_id": "transfer-001",
  "files": [
    { "file_id": "file-001", "filename": "photo.jpg", "size": 123456, "sha256": "..." }
  ]
}
```

The receiver uses the manifest to verify the received files. The sender's stored hash is authoritative.

---

# ADR-052: Canonical Computer Vision Event Names

**Status:** Accepted

Phase 2 computer-vision integration uses one canonical event vocabulary:

```text
pointer_move   pointer_click   pointer_down   pointer_up
selection_changed
drag_start     drag_move       drag_end
grab           release
gesture_detected
direction_detected
```

`gesture_detected` carries a gesture state, for example `{"event": "gesture_detected", "gesture": "closed_hand", "confidence": 0.94}`.

`direction_detected` carries a direction, for example `{"event": "direction_detected", "direction": "right", "confidence": 0.91}`.

### Rationale

Phase 2 CV development must not create multiple competing event naming schemes. The CV implementation maps its internal model to these canonical events. The earlier uppercase names (`HAND_CLOSED`, `POINTER_MOVE`, ...) are superseded.

---

# ADR-053: Phase-1 Implementation Stack and Mechanisms

**Status:** Accepted

The documentation did not name these items; they are fixed here so implementation does not drift.

| Concern | Decision |
|---|---|
| UI | Vanilla TypeScript + Vite inside Tauri v2 |
| Peer API server | FastAPI + uvicorn, HTTPS only |
| Peer API client | httpx |
| Discovery | `zeroconf` (service `_handoff._tcp.local.`) |
| Cryptography | `cryptography` (Ed25519 identity keys, self-signed TLS certificate) |
| Python tooling | uv, ruff, mypy, pytest, PyInstaller (sidecar bundle) |

**Phase 2 note (ADR-045):** mechanisms 4 (Receive Mode), 5 (collisions on received files: now real filenames in the receiver-chosen folder), 9 (readiness check) and 13 (received files live outside the data directory) are amended by ADR-054/055.

### Mechanisms

1. **IPC:** JSON-lines `{id, action, payload}` → `{id, result | error}` over the sidecar's stdin/stdout. The UI polls a `status.snapshot` action about once per second for connection, Receive Mode, active-transfer progress and recent history. The UI never calls peers directly.
2. **Identity and TLS:** each install generates its own Ed25519 key and a self-signed certificate. The client pins the server's public key (first connect: fingerprint from mDNS TXT record; afterwards: `devices.public_key`). The server authenticates each request through an Ed25519 signature over method, path, device ID, timestamp, nonce and transfer ID, with a ±60 s clock window and nonce replay rejection.
3. **Trust:** the user clicking Connect makes the peer trusted on both sides, with no approval prompt (FR-011). Requests from non-trusted devices are rejected and audited as `INVALID_DEVICE`.
4. **Receive Mode** is changed only through local IPC. Peers can read it but never change it.
5. **Filename collisions (ADR-020):** files are stored on disk under UUID names, so nothing on disk is overwritten. The displayed `original_name` becomes `name(1).ext`, `name(2).ext`, ... when an active file with the same name already exists.
6. **Crash recovery:** on startup any transfer in a non-terminal state is marked `failed` and its temporary directory removed. An accepted transfer that receives no data within a timeout is marked `failed`.
8. **One peer, both directions:** an inbound `POST /connection` from a different device is refused (`DEVICE_ALREADY_CONNECTED`) while another peer is connected and online. A trusted device may send only if it is the connected peer, or if nobody is connected (for example after a restart, when trust persisted but the connection did not).
9. **Readiness before sending (FR-023):** the sender checks the peer's Receive Mode first; if OFF, `transfer.create` fails immediately and creates no transfer or history entry.
10. **Offline detection:** the connected peer is health-checked every few seconds; after three misses it is marked offline, a running upload is aborted, and the transfer is failed. When the peer answers again it is restored without a new handshake. Address changes are picked up from discovery.
11. **Requests while idle:** the IPC layer runs requests on a small worker pool so a slow `devices.connect` never blocks status polling.
12. **Abuse limits:** replayed nonces, bad signatures, invalid manifests and oversized or stalled uploads are rejected, audited, and rate limited per address/device.
13. **Data directory:** the core resolves the OS app-data directory itself (`%APPDATA%\HandOff`, `~/.local/share/HandOff`); Tauri passes nothing in release builds. The Windows uninstaller hook removes exactly that folder (ADR-043). An AppImage has no uninstaller, so on Linux data removal is a documented manual step (`rm -rf ~/.local/share/HandOff`).
14. **Sidecar packaging:** the core is a PyInstaller onedir bundle shipped as a Tauri *resource* (`handoff-core/`), not `externalBin`, because `externalBin` carries a single file and cannot hold a onedir bundle. Release builds launch it with no environment overrides; the debug-only `HANDOFF_*` overrides are compiled out.

### Rationale

Request signatures are fully testable and do not depend on uvicorn exposing client certificates to the application layer, while still satisfying the requirement that identity must be verified in addition to encryption.

---

# ADR-054: Edge Transfer UX (Phase 2)

**Status:** Accepted

**Supersedes / amends (ADR-045):** ADR-011 (Send / Receive Mode), ADR-012, ADR-023 (gallery deletion), ADR-033 and ADR-035 (Phase 2 is no longer computer vision), and the gallery, Send button and Receive Mode parts of REQUIREMENTS §2, §7, FR-005..FR-008, FR-017..FR-023, FR-036, FR-037.

**Decision:** Phase 2 replaces the gallery window with an *edge transfer* interaction.

1. HandOff is a thin, borderless, always-on-top strip attached to the **right edge** of the screen. Its width is a DPI-aware logical size of about 2 cm (never a hard-coded physical measurement). Idle, it is nearly invisible and click-through.
2. The Send interaction is **drag a file from the OS to the right edge, then release**. Native OS drag-and-drop (mouse) is the only input in this phase. There is no camera, OpenCV, MediaPipe or gesture code.
3. A drop is sent **automatically to the one connected trusted peer** (ADR-046). There is no device-selection dialog. With no connected peer the drop is rejected ("No HandOff device connected") and nothing is queued.
4. The gallery, the Send button and the Receive Mode toggle are removed. Device discovery, connecting, the destination folder and history live in a compact panel that opens when the edge is clicked.
5. The UI is split into an **input layer** (OS drag-and-drop, edge proximity) that emits the canonical ADR-052 semantic events, an **explicit edge state machine**, and an **animation layer** that reacts only to state. A later computer-vision input becomes one more adapter feeding the same events, state machine and animations.
6. The edge never reports success before the backend reports a terminal state. Animations follow real transfer state, which the core pushes to the UI as events (polling remains a slow fallback).
7. **Computer vision moves from "Phase 2" to "Phase 3".** ADR-033 and ADR-035 keep their text; only the phase label changes. The CV integration boundary (API §38-§42, `cv/events.py`) stays defined and disabled.

### Consequences

- The transfer engine, trust model, manifest, ZIP, SHA-256, state machine, audit and history are unchanged.
- Dropped files still go through `files.import` (copy and hash, ADR-049) and `transfer.create` by file ID (ADR-050). The managed copy of a dropped file is logically deleted once its transfer reaches a terminal state. History and audit rows remain.
- ADR-014 ("sender selects the destination device") is satisfied by ADR-046: the destination is the connected peer and the UI always shows it.
- Wayland sessions cannot position a global always-on-top strip. HandOff runs through XWayland there (documented limitation).

---

# ADR-055: File Types, Receive Mode Removal and Receiver-Chosen Destination

**Status:** Accepted

**Supersedes / amends (ADR-045):** ADR-013 (the gate for automatic receiving changes), ADR-018 (file types), ADR-022 and ADR-041 "Filesystem Isolation" (where received files are written), ADR-053 mechanisms 4, 5, 9 and 13, SECURITY §19 / §21 / §29 / §33 / §55 #7.

**Decisions:**

1. **File types.** Allowed extensions are `.txt .jpg .jpeg .png .pdf`, matched case-insensitively, on both sender and receiver. `.mp4` and `.exe` are no longer accepted. Files whose content starts with a PE (`MZ`) or ELF header are rejected even if renamed. The 50 MB limit (ADR-017) is unchanged. Directories, symlinks, shortcuts and non-regular files are rejected. If any item in a drop is invalid the whole drop is rejected.
2. **Receive Mode is removed.** There is no toggle, no `receive_mode` API field and no `GET /api/v1/receive-mode`. A trusted connected peer is always able to send. The trust, identity and connected-peer checks remain the gate. The `receive_mode` settings row is **obsolete**: it is left in existing databases, ignored, and never deleted. The error code `RECEIVE_MODE_DISABLED` is retired.
3. **Receiver-chosen destination.** Received files are written to a **receiver-local folder**, the `receive_directory` setting. If unset, it is the OS Desktop folder (resolved through the OS, never a hard-coded username). The user changes it at any time with the native folder picker. There is no per-transfer prompt, because the receiving side of a transfer is one synchronous request (API §22). The sender can never specify or see a path on the receiver.
4. **Destination safety.** The folder must be an absolute path to an existing, writable directory, validated by the receiver itself when the setting is saved and again before each transfer (otherwise `RECEIVER_NOT_READY`). Final names are made unique with `name(1).ext`, `name(2).ext`, and so on, reserved atomically (exclusive create), so existing files are never overwritten. Each file's SHA-256 is verified again while it is copied into the destination.
5. **Received files are no longer managed storage.** They get no `files` row; history lives in `transfers` and `transfer_files` (`file_id` is NULL). Because they live outside the app-data directory, they **survive uninstall** (ADR-043 covers HandOff-managed data only). Files received under Phase 1 remain in `received/`.
6. **Breaking peer-API change.** The type set and the removal of Receive Mode change the peer contract. Both peers must run HandOff 0.2.x or later.

### Rationale

The user decides where their own files land, on their own machine, through a native picker. Letting the receiver choose, rather than the sender, keeps the rule "never trust a network-supplied path" intact.

---

# ADR-056: Hand Control (Phase 3, step 1)

**Status:** Accepted

**Supersedes / amends (ADR-045):** ADR-054 items 2 and 7 (no camera, OpenCV or MediaPipe in Phase 2), ADR-033 and ADR-035 (phase label), CLAUDE.md "Phase 3 boundaries" for hand tracking only, API §38-§42 (the CV channel).

**Decisions:**

1. **Scope.** Phase 3 starts with *hand control*: a webcam hand tracker that moves the OS pointer and presses/releases the primary mouse button (pinch = button down, release = button up, short pinch tap = click). Spatial targeting, multiple peers, files over 50 MB, cancellation, WAN and everything else on the Phase 3 exclusion list stay out of scope.
2. **The send path is unchanged.** A hand-held drag is a real OS drag. The file still arrives as `OS drop → input/osDrag.ts → edge/machine.ts → drop.send → files.import`, with every validation of ADR-054/055. The camera never names a file, never calls a peer and never calls `transfer.create`. (Amended by ADR-057: the core may start the same pipeline after a validated `release` event.) Why not CV-only events: no API can "grab the file under a virtual pointer" on the desktop, so a pure event design would need an in-app file tray, which reverses ADR-054.
3. **Canonical events.** The tracker also reports ADR-052 events to the core. The UI receives only feedback events (`gesture_detected`, `direction_detected`) with `source: "cv"`. `grab`, `release` and `drag_*` are never forwarded to the state machine, because the OS adapter already reports the real drag and a duplicate `release` without paths would be a false "invalid drop".
4. **Process model.** The tracker runs as a child process of the core (`python -m handoff --cv-worker`, same bundle). It speaks JSON lines on stdout and exits when its stdin closes. A MediaPipe crash cannot affect transfers. API §42's `POST /internal/v1/cv/events` stays **disabled**: nothing CV-related is reachable on the LAN.
5. **Opt-in.** The setting `hand_control_enabled` (default `false`, persisted) is the only switch. The camera is opened only while it is on. Frames are processed in memory and are never stored, logged or sent.
6. **Dependencies (approved by this ADR).** `mediapipe` (brings `opencv-contrib-python` and `numpy`) and `pyautogui`. The hand-landmarker model is fetched at **build time** with a pinned SHA-256 and bundled; the app never downloads it. If it is missing, hand control reports an error and stays off.
7. **Safety.** If the hand disappears mid-drag, the worker presses Esc (cancelling the OS drag) before releasing the button, so a file is never dropped by accident onto another folder. On exit and crash the supervisor releases the button.
8. **Limits.** X11/XWayland and Windows only (no native Wayland injection); primary monitor only; Windows untested by the author.
9. **Pointer model (amended, ADR-045).** The cursor is *relative*, like a touchpad: it moves by the change in hand position times a gain that rises with hand speed (slow = precise, a brisk flick crosses the screen). Taking the hand out of view is "lifting the finger": when it returns the cursor continues from where it was (resynced from the real cursor) and does not jump to the hand. This replaces the first version's absolute mapping of the camera frame onto the screen, which forced the hand to travel across the whole frame.

### Rationale

Hand control is one more input mechanism (DECISIONS §47). It drives the same OS drag the mouse does, so the transfer engine, trust model and validation are untouched.

---

# ADR-057: Pointing-Only Cursor and the COPY Gesture (Phase 3, step 2)

**Status:** Accepted

**Supersedes / amends (ADR-045):** ADR-056 decision 1 (pointer moves whenever a hand is visible), decision 2 and the statement "never calls transfer APIs" (the send path gains a second trigger), decision 3 (`grab` / `release` are now consumed by the core).

**Decisions:**

1. **Pointing-only cursor.** The cursor follows the hand only while the hand *points*: index finger extended, middle, ring and pinky curled. An index curled into a pinch with the thumb still counts (it is the click), a fist does not. The pose must hold briefly (on) and be lost briefly (off), so a flicker does not stutter the cursor. Leaving the pose is a touchpad "lift": the cursor stays and resumes from where it was. A pinch already in progress keeps following, so a drag never freezes.
2. **Click and drag are unchanged.** Index + thumb touching is the primary button. Holding the pinch while moving is a drag/select. They only start while pointing.
3. **COPY gesture.** Open palm then closed palm (held ~0.25 s each, within 1.5 s) is a **grab**: the worker presses Ctrl+C (plain OS input, like the pointer) so the file manager copies the selection. Closed then open palm is a **release**: it sends what was copied. Anywhere, with no spatial targeting: the destination is always the one connected peer (ADR-046). The hand may leave the camera view while it carries the grab. A grab expires after 20 s; after a release there is a 2 s cooldown. Release with no prior grab does nothing.
4. **The worker still never names a file and never calls a peer.** It reports only `grab` / `release` (ADR-052 names). The **core** then reads the file list from the OS clipboard (`cv/clipboard.py`: Windows `CF_HDROP` via ctypes; Linux `text/uri-list` / `x-special/gnome-copied-files` via tkinter, no new dependency) and calls the existing `drop.send` (`files.import` -> `transfer.create` -> signed TLS). Every ADR-054/055 rule applies unchanged on both sides: allowed types, <= 50 MB, no folders/symlinks/renamed executables, all-or-nothing, no peer = rejected (never queued), one active transfer.
5. **Clipboard is untrusted.** Only absolute local `file:` URIs are accepted; the result goes through the normal drop validation. The clipboard is read only after a grab from the worker, never otherwise, and never stored or logged.
6. **Feedback.** The worker reports `palm_grab` / `palm_release` the moment the palm closes / opens, and the edge strip plays a short grab (pinch in) or release (swell out) animation; they are feedback only, never a transfer state, and never interrupt a drag or a transfer. The core publishes `gesture_detected` with `copied`, `copy_empty`, `copy_failed`, `sent`, `send_failed` (`source: "cv"` in the UI). `grab` / `release` are not forwarded to the UI state machine.
7. **Known limits.** If nothing is selected when the hand grabs, Ctrl+C copies nothing and an *earlier* clipboard file list could be picked up; it is still fully validated and the user made the gesture. Ctrl+C goes to the focused window (a terminal would receive an interrupt). Linux needs `tkinter` in the bundled Python (checked at runtime: `CLIPBOARD_UNAVAILABLE`). Windows path untested by the author.

### Rationale

The user asked for a gesture that copies a selected file and sends it. A pure-CV design cannot see the OS selection, and the existing drop path already holds every validation, so the gesture only produces the input (Ctrl+C) and a trigger; the engine is untouched.

---

# Documentation Authority

When documentation conflicts, use this priority:

```text
DECISIONS.md
    ↓
REQUIREMENTS.md
    ↓
SECURITY.md / DATABASE.md / API.md
    ↓
ARCHTECTURE.md
    ↓
Implementation
```

A lower-level document or implementation must not silently override an accepted decision.

---

# 46. Current Architectural Direction

The current Phase-1 architecture can be summarized as:

```text
                 ┌─────────────────────┐
                 │      HandOff        │
                 │                     │
                 │      Tauri UI       │
                 │          │          │
                 │          ▼          │
                 │    Python Backend   │
                 │          │          │
                 │    ┌─────┼─────┐    │
                 │    │     │     │    │
                 │    ▼     ▼     ▼    │
                 │ SQLite Files Network│
                 │                     │
                 └─────────┬───────────┘
                           │
                         LAN
                           │
                 ┌─────────▼───────────┐
                 │      HandOff        │
                 │    Other Device     │
                 └─────────────────────┘
```

The architecture deliberately prioritizes:

```text
Simple
        ↓
Local
        ↓
Fast
        ↓
Secure
        ↓
Extensible
```

over premature complexity.

---

# 47. Core Principle

The most important architectural decision is:

> **Build the reliable file-transfer engine first. Build the futuristic computer-vision interaction on top of it later.**

The CV system must become another input mechanism for HandOff rather than becoming the foundation of the transfer engine.

This separation allows Phase 1 to remain stable while Phase 2 introduces the hand-controlled interaction model.

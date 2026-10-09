# CLAUDE.md — HandOff

`docs/` is the detailed source of truth. This file is the working summary. Conflict priority:
**DECISIONS.md → REQUIREMENTS.md → SECURITY/DATABASE/API.md → ARCHTECTURE.md → implementation.**
Fix drift in the docs; don't build around it. Lower levels never silently override an ADR.

## Project Purpose
HandOff is a **native desktop app** for Windows x64 and Linux x64. It transfers files
directly between nearby computers on the **same LAN/Wi-Fi**. Phase 1 built a reliable
peer-to-peer transfer engine. Phase 2 replaced the gallery UI with the edge drop strip. Phase 3 adds a hand-gesture (computer vision) interface on top of it; step 1 (opt-in hand control, ADR-056) is being built. Core principle (DECISIONS §47): **build the transfer engine first. CV is just
another input mechanism later and must never become the foundation.**

## Phase 2 (Edge Transfer UX) — current product (ADR-054/055; supersedes conflicting Phase 1 rules below)
- The gallery, Send button and Receive Mode are **gone**. HandOff is a thin always-on-top **right-edge** strip: drag files from the OS to the edge, release, and they go to the **one connected trusted peer**. No peer → reject ("No HandOff device connected"); never queue.
- Allowed types: `.txt .jpg .jpeg .png .pdf` (case-insensitive, ≤ 50 MB). `.exe`/`.mp4` removed. Reject folders, symlinks/shortcuts, non-regular files, and executables renamed to an allowed extension (PE `MZ` / ELF header) — on **both** sender and receiver. A drop is **all-or-nothing**.
- Pipeline is unchanged: `drop.send` → `files.import` (copy + SHA-256) → `transfer.create` (file IDs) → existing signed TLS protocol. Dropped managed copies are logically deleted when the transfer is terminal; orphans are swept at startup.
- **Receiver chooses where files land**: setting `receive_directory` (default OS Desktop via `platformdirs`), picked with the native folder dialog. Validated by the receiver only (absolute, existing, writable, outside HandOff's data dir). Never from the network. Files are written with exclusive create (`name(1).ext`, never overwrite) and re-hashed during the copy. Received files are **not** managed storage (no `files` row; `transfer_files.file_id` NULL).
- `receive_mode` setting is obsolete: keep the row, ignore it, never delete it. `GET /receive-mode`, `RECEIVE_MODE_DISABLED` and the IPC actions are removed.
- UI layering (keep it): input adapters (OS drag-drop, edge proximity) → ADR-052 semantic events → pure state machine `edge/machine.ts` → view/animations. Success views are reachable **only** from a backend `completed`. The core pushes `transfer.updated` / `connection.changed`; polling is a slow fallback.
- Phase 3 step 1 (ADR-056): opt-in hand control. A child process (`handoff --cv-worker`, `backend/src/handoff/cv/`) tracks the hand with MediaPipe and moves the OS pointer; the pointer is relative like a touchpad (hand out of view = lift and reposition), the cursor moves only while the index finger points (ADR-057); index+thumb touching is the mouse button, so a hand drag is an ordinary OS drag into the existing drop path. COPY gesture (ADR-057/058): open→closed palm on laptop 1 presses Ctrl+C and the core holds the file list (an open palm on laptop 1 only cancels); when the closed hand is carried to laptop 2 and opens there, laptop 2 sends a signed `POST /api/v1/handoff/claim` and laptop 1 sends through the normal `drop.send` pipeline. The worker itself never names a file or calls transfer APIs. Palm grab/release also play full-screen effects at the cursor in a click-through overlay window `fx` (ADR-059; feedback only; hand-and-photo artwork per ADR-062). The strip shows what is held (`hand.held`, names only) and a browser "Copy image" picture is held as a temporary PNG (ADR-060). Setting `hand_control_enabled` (default off) is the only switch; frames are never stored or sent; the model is bundled, never downloaded. UI gets only `gesture_detected`/`direction_detected` with `source: "cv"`.
- Auto-open (ADR-061): setting `auto_open_received` (default off, local only). When on, the receiver opens verified stored files (allowed types only, max 5 per transfer) in the OS default app after the transfer is committed; it never executes anything and never affects the transfer outcome.
- Window rules: Wayland is forced to XWayland; transparency needs a compositor; Windows is untested by the author.

## Phase 1 Scope (MVP) — historical; where it conflicts with Phase 2 above, Phase 2 wins
- Tauri desktop window at ~80% opacity. The same app runs on every device; there is no sender/receiver split.
- Add files through the app's file-add mechanism. Only `.txt .jpg .mp4 .exe` are allowed, and extension matching is case-insensitive.
- Each file must be **≤ 50 MB** (50 MB = 52,428,800 bytes). Reject larger files with a clear reason.
- Importing **copies** the file into app storage. Never touch the original.
- Gallery shows icon + name. Selection supports single click, Ctrl, Shift, and drag-select.
- Deleting a file is a logical delete of HandOff's managed copy. The user's original is never touched.
- Discover devices with mDNS. Each install has a persistent device ID. Many devices can be discovered, but there is **exactly one connected peer** at a time (ADR-046). Connections are bidirectional.
- There is **no Disconnect button**. Choosing another device does an internal graceful disconnect, then connects to the new one (ADR-047). **During an active transfer, reject the switch** until the transfer reaches `completed`, `failed` or `partially_completed`. Never interrupt a transfer silently.
- Detect when the peer goes offline.
- Receive Mode toggle (ON/OFF):
  - It can be turned ON with no peer connected, and its state persists across restarts.
  - ON + trusted peer means transfers are auto-accepted, with no per-file prompt.
  - OFF means transfers are rejected with `RECEIVE_MODE_DISABLED`.
- Send is enabled only when ≥1 file is selected AND a peer is connected. The UI must show the destination explicitly.
- Multiple files go out as **one transfer job** packaged in a temporary ZIP. Transfers use copy semantics. Only **one active transfer** at a time, and transfers can't be cancelled.
- Name collisions get `photo(1).jpg`, `photo(2).jpg`, and so on. Never overwrite. Identical content is still copied (no dedup).
- Transfer history persists, and the user can configure how long it is kept. Desktop notifications fire for completed, received, and failed transfers.
- Leave a CV integration boundary in place, defined but **not enabled**.

**Definition of Done:** the end-to-end workflow in `docs/REQUIREMENTS.md` §31 works reliably on Windows ↔ Linux.

## Technology Stack (docs + ADR-053)
| Concern | Choice |
|---|---|
| Desktop shell / UI | Tauri v2 + vanilla TypeScript/Vite (packages the whole app) |
| App core | Python, launched and managed by Tauri as a local sidecar process |
| UI ↔ core | Local IPC (structured stdin/stdout or equivalent). **Never exposed to the LAN.** |
| Peer ↔ peer | FastAPI/uvicorn (server) + httpx (client): HTTP/REST `/api/v1` over **TLS**, with streamed file data |
| Discovery | mDNS service `_handoff._tcp.local` via `zeroconf` |
| Database | SQLite through **SQLAlchemy 2.x** behind a repository layer. **No Alembic.** |
| Integrity | SHA-256 |
| Tests | pytest (pytest-asyncio, pytest-cov, and httpx as needed) |
| Packaging | Windows installer + Linux AppImage, with a bundled Python runtime and deps |

Stack choices beyond this table are architecture decisions: ask, don't pick silently.
IPC is JSON-lines over stdin/stdout. Identity is Ed25519 plus signed requests (ADR-053).

## Architecture
```
Tauri UI ──IPC──► Python Core (File/Device/Discovery/Connection/Transfer/Receive/
                  History/Storage managers + CV Integration Layer)
                    ├─► Repositories ─► SQLAlchemy ─► SQLite (metadata only)
                    ├─► App-managed filesystem storage (binaries)
                    └─► Network layer (mDNS + HTTPS REST + streaming) ──LAN──► peer
```
- The Python core is authoritative. The UI holds no transfer logic, never touches the DB, and **never calls peer APIs directly**. It gets progress through IPC from the local core.
- Keep perception, application logic, networking, and storage separate (ARCHTECTURE §29).
- Transfer states are `created → validating → accepted → transferring → completed`. Any state can go to `failed`, and `transferring` can go to `partially_completed`. Reject every other transition. **There is no `CANCELLED` state.**
- Per-file states are `pending, transferring, completed, failed`.

## Security Rules (Trusted-LAN model — `docs/SECURITY.md`)
- A device becomes trusted only when the user explicitly clicks Connect. Trust persists across restarts. There is no remote approval prompt and no passwords, OTP, or accounts.
- Each install generates its own key pair. The private key never leaves the machine and is never logged, sent, or shipped in a build. Store it with restrictive permissions.
- All peer traffic uses TLS with **identity verification**. Encryption alone isn't enough. Plaintext transfer must not be possible in production.
- Check order: HandOff device → valid identity → trusted → Receive Mode ON → valid request. Reject unknown devices and write an `INVALID_DEVICE` audit entry.
- Treat all network input as untrusted. Don't trust the sender's filenames, sizes, or hashes. Enforce extension and size limits on **both** sender and receiver.
- Before extracting a ZIP, validate every entry: normalize it, resolve it, and confirm it stays inside HandOff storage. If any entry escapes, reject the **whole** transfer. Sanitize filenames.
- Trust is persisted as `devices.public_key` + `devices.is_trusted` (ADR-048). The private key is never stored in the DB.
- SHA-256 is computed when a file **enters managed storage**, not at transfer time (ADR-049). The sender's stored hash is authoritative.
- The sender's backend builds a transfer **manifest** with `file_id`, `filename`, `size` and `sha256` for each file (ADR-051). The receiver recomputes each hash and compares it to the manifest. On mismatch, fail the transfer and write an `INVALID_HASH` audit entry.
- Reject replayed transfer IDs. Enforce `MAX_FILES_PER_TRANSFER` as a configurable constant. Check free disk space before accepting.
- `.exe` files are ordinary data: **never execute** received files. No antivirus scanning.
- Remote peers must not be able to call local or admin functions, including toggling the local Receive Mode. Keep debug/dev endpoints out of production.
- Don't modify the firewall. The OS may prompt the user on its own; that's fine.

## Database / Storage Rules (`docs/DATABASE.md`)
- There are six tables: `devices, files, transfers, transfer_files, settings, audit_logs`. Don't add tables for future (CV) features.
- No binaries in SQLite. Use UUID application IDs. `device_id` is never derived from IP, MAC, or hostname.
- Store `original_name` (shown to users) separately from `stored_name` (a UUID on disk). Store `sha256` for every file.
- Logical deletion sets `files.deleted_at`. Transfer history and audit logs must survive file deletion, so no cascading deletes into history.
- Run `PRAGMA foreign_keys = ON`. Keep transactions short. Commit each logical state change (transfer + transfer_files + audit) atomically.
- The filesystem and the DB can't be atomic together. Use explicit state transitions, and on any failure mark the transfer `failed`, never `completed`.
- Only mark `completed` once the archive is fully received, validated, extracted, and hash-verified, the files are in final storage, and the DB is updated.
- Data lives in the OS app-data dir (`%APPDATA%/HandOff/`, `~/.local/share/HandOff/`), never the install dir. Layout: `handoff.db, files/, received/, transfers/, temp/`. Never hardcode absolute paths.
- Clean up temporary archives on both success and failure.
- History-retention cleanup runs periodically and **never deletes `audit_logs`**.
- If the DB fails to open, log it, show a friendly error, and never silently create a second DB.
- Schema changes:
  - Update the models, `DATABASE.md`, and the tests.
  - Write an in-app upgrade routine at DB init for existing databases.
  - Never cause silent data loss.

## API / Development Rules (`docs/API.md`)
- The peer API is a **contract**. Don't rename endpoints, change response shapes, or add transfer states without updating `API.md`. Record any breaking change in `DECISIONS.md`.
- Every error uses the `{"error": {"code", "message", "details?"}}` shape, with codes from API.md §33 and conventional HTTP status codes (409 when Receive Mode is off, 413 when a file is too large).
- Stream uploads to disk and never load a whole file or archive into memory. Never put binaries in JSON.
- Use ISO 8601 timestamps. Support `X-Request-ID` and an idempotency key on transfer creation.
- Distinguish application errors from network errors. Transfers must never block the UI thread.
- A failed health check isn't proof a peer is offline. Apply retry and timeout first, then mark it offline promptly.
- Transfer creation takes **file IDs only**. The backend looks up the stored name, size and hash. Never accept client-supplied hashes (ADR-050).
- Leave `POST /internal/v1/cv/events` (API §42) reserved and **disabled**. CV may never bypass the application state machine. Phase 3 must use only the canonical CV event names in ADR-052:
  - `pointer_move/click/down/up`, `selection_changed`, `drag_start/move/end`
  - `grab`, `release`, `gesture_detected`, `direction_detected`
- Device names come from the OS. Users can't edit them in Phase 1.

## Testing Requirements (`docs/TESTING.md`)
- Never declare a feature done without tests. Test the failure paths, not just the happy path.
- Use an isolated temp DB and temp storage for every test. Never touch the real DB or user files, and never require Internet.
- Required coverage:
  - the 50 MB boundary (0 B, 1 B, 49.9 MB, exactly 50 MB → accepted, >50 MB → rejected)
  - extension case handling
  - path traversal, including inside archive entries
  - duplicate names
  - SHA-256 mismatch
  - unknown and invalid device identity
  - replayed transfer IDs
  - one-active-transfer
  - partial completion
  - peer going offline mid-transfer
  - sender and receiver crashes
  - persistence across restarts
  - audit log entries
- Coverage targets are ≥80% for the backend and ≥90% for transfer, security, trust, filesystem, validation, and state-transition code. Never cut security tests to raise the number.
- Before a substantial commit, run lint, type check, and the relevant unit and integration tests. Before calling a feature complete, run the full suite, the security tests, and coverage.
- Add a regression test for every meaningful bug fix. Name tests by the behavior they check.

## Deployment Constraints (`docs/DEPLOYMENT.md`)
- Desktop only. No cloud, central server, VPS, Docker host, or remote DB.
- Users must not need Python. Bundle the runtime and dependencies at build time, with no first-run downloads.
- Builds are unsigned, ship on a stable-only channel, use semver `0.x.x`, and update manually (no auto-update). No `.deb` package.
- Upgrades preserve all data. Uninstall removes only HandOff's app-data dir and never touches other user directories.
- Production builds include no debug UI, dev endpoints, dev keys or certs, mock devices, or secrets.
- GitHub Actions release automation is deferred (ADR-044).

## Phase 3 (CV) boundaries — beyond ADR-056, do NOT build yet
(Allowed since ADR-056: camera, OpenCV, MediaPipe, hand detection, gesture recognition, hand-controlled pointer and gesture drag/drop, in the form described under Phase 2/3 above.)
Still out: spatial targeting, multiple simultaneous peers, Internet/WAN
transfer, cloud storage or sync, mobile apps, files over 50 MB, transfer cancellation,
user accounts, a disconnect/untrust UI, auto-update, code
signing, and antivirus. "It would be cool if…" doesn't expand scope (REQUIREMENTS §32).
Phase 3 CV must plug in through the CV Integration Layer **without rewriting** the
transfer engine.

## Critical Architectural Decisions (`docs/DECISIONS.md`)
ADR-001 Tauri + Python (not Electron or web) · 002/003 P2P, LAN-only, no server ·
004 Win/Linux x64 only · 005/006 one package with bundled Python · 007 SQLite ·
008 persistent device ID · 009/040/041 trusted-LAN model, no auth system · 013 auto-receive ·
015 one active transfer · 016 multiple files = one job · 017 50 MB · 018 four file types ·
019 SHA-256 · 020/042 never overwrite · 022 separate app storage · 023 logical delete ·
024/025 audit logs never deleted · 026 no AV · 027 no firewall management · 033 CV in Phase 3 (amended by 054) ·
046 one peer · 047 switch = internal disconnect, blocked during an active transfer · 048 `public_key` + `is_trusted` ·
049 hash at import · 050 transfer takes file IDs · 051 manifest with SHA-256 · 052 canonical CV events.
To change any ADR, follow ADR-045: name the ADR, give the reason and the replacement,
update the docs. **Never change architecture silently.**

## Doc Conflicts — Resolved in the docs (M0, ADR-046–053). Kept for reference
| Conflict | Resolution |
|---|---|
| Size limit `≤ 50 MB` (REQ, TESTING) vs `< 50 MB` (SECURITY) | ≤ 50 MB, per ADR-017's "maximum of 50 MB" |
| Collision naming `photo (1).jpg` (REQ, API) vs `photo(1).jpg` | `photo(1).jpg`, per ADR-020 |
| `CANCELLED` status (ARCHTECTURE §18) | Doesn't exist. No cancellation (FR-026). Use the API/DATABASE state set. |
| Storage layout `files/imported`, `database/application.db` (ARCHTECTURE) | Use the DATABASE/DEPLOYMENT layout (`handoff.db`, `files/`, `received/`, `transfers/`, `temp/`), matching ADR-022 |
| Endpoint names `/device/info`, `/transfer`, … (ARCHTECTURE §13) | API.md is authoritative: `/api/v1/device`, `/transfers`, … |
| Plain "HTTP/REST" (REQ NFR-003) vs mandatory TLS (SECURITY) | HTTP/REST over TLS, per ADR-041 (encrypted communication) |
| UI polls peer `GET /transfers/{id}` (API §21) vs UI never calls peers (API §31, §44) | The UI gets progress from the local core over IPC |
| Peer-callable `PUT /receive-mode` (API §13) vs SECURITY §47 | Receive Mode is changed only locally by the user (ADR-012) |
| Audit retention "unless policy applies" (DATABASE §34) | Audit logs are never deleted by normal operations (ADR-025) |
| Installer "requests firewall permission" (SECURITY §45) | No firewall handling. Only the OS's own prompt (ADR-027, DEPLOYMENT §17) |
| "Multiple connected devices" (ADR-014, TESTING §50); peer switching (ARCHTECTURE §11) | One peer, with internal switching (ADR-046/047). ADR-014 is met by always showing the destination |
| `devices` schema has no trust columns (DATABASE §9) | Add `public_key` and `is_trusted`; keep existing names `device_id` and `last_ip` (ADR-048) |
| `POST /api/v1/transfers` body (API §15) has no hashes | Internal create takes file IDs (ADR-050); the peer receives a manifest with SHA-256 (ADR-051). Update API.md first |
| CV names `HAND_CLOSED`, `POINTER_MOVE`… (API §39–40, ARCHTECTURE §27) | Superseded by the canonical lowercase names in ADR-052 |

## Non-Negotiable Rules
1. Never build anything listed under Phase 3 boundaries. Hand control is allowed only as ADR-056 defines it (opt-in, OS-pointer; the worker never calls transfer APIs; the COPY gesture sends only via the core's validated `drop.send` after a valid peer claim, ADR-057/058).
2. Never add cloud services, a central server, user accounts or authentication, antivirus, firewall changes, or auto-update.
3. Never accept a file over 50 MB or one outside `.txt .jpg .jpeg .png .pdf`. Check on both sides.
4. Never overwrite an existing file. Never modify or delete the user's original file.
5. Never write outside HandOff storage, trust a network-supplied path, or execute a received file.
6. Never accept transfers from untrusted devices (there is no Receive Mode any more).
7. Never send peer traffic in plaintext or skip identity verification.
8. Never report `completed` before full receipt, validation, SHA-256 verification, final storage, and the DB update.
9. Never store binaries in SQLite. Never let UI code touch the DB or peer APIs.
10. Never allow more than one active transfer or more than one connected peer. Never switch peers during an active transfer.
11. Never accept file hashes from the client or frontend. The stored hash computed at import is authoritative.
12. Never delete audit logs or history as a side effect of file deletion. Never silently destroy DB data during schema changes.
13. Never log, transmit, commit, or ship private keys or secrets. Each install generates its own identity.
14. Never change the API contract, schema, or an ADR without updating the matching doc (and DECISIONS.md for breaking or architectural changes).
15. Never weaken or delete a failing test. Never declare a feature done without passing tests.

## Which doc to read
| Question | Read |
|---|---|
| Is X in scope? Acceptance criteria? UI behavior? | `docs/REQUIREMENTS.md` (§3 exclusions, §30 criteria, §31 DoD) |
| Components, flows, layers, CV boundary | `docs/ARCHTECTURE.md` (filename is misspelled) |
| Endpoints, payloads, error codes, states, CV events | `docs/API.md` |
| Tables, columns, indexes, deletion, retention, schema changes | `docs/DATABASE.md` |
| Trust, TLS, validation, path safety, audit events | `docs/SECURITY.md` |
| What and how to test, coverage, release checklist | `docs/TESTING.md` |
| Packaging, bundling, app-data paths, upgrade/uninstall, release | `docs/DEPLOYMENT.md` |
| Why something is the way it is, or how to change it | `docs/DECISIONS.md` (wins all conflicts) |

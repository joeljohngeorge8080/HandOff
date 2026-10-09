# Testing Strategy

> **Phase 3 amendment (ADR-056).** Hand control tests (no camera needed): `unit/test_cv_filters.py`, `test_cv_pose.py` (pointing-only), `test_cv_relative.py`, `test_cv_palm.py` (COPY gesture), `test_cv_copy_bridge.py` (clipboard parsing, hold/cancel/claim bridge), `api/test_handoff_claim_api.py` (signed, trusted-peer-only claim), `e2e/test_copy_gesture.py` (grab on one node, release on the other), `test_cv_gestures.py` (tap vs hold, hysteresis, no click after a drop, Esc on hand loss mid-drag, reacquire snap), `test_cv_supervisor.py` (fake worker: bad lines and non-canonical events dropped, rate limit, crash -> error with no respawn, Wayland and missing-dependency refusals, setting persists), settings validation; frontend `fx/effects.test.ts` + `fx/timeline.test.ts` (effect geometry, bounded timeline, payload validation), Rust `edge/fx.rs` unit tests (event filtering, screen and cursor maths), `input/cv.test.ts` (only `gesture_detected` / `direction_detected` forwarded; `release`/`drag_*` never reach the machine). Not automated: a real webcam and a real hand-held OS drag (manual), and Windows.

> **Phase 2 amendment.** §21 and §51 (Receive Mode tests) are replaced by edge/drop tests: extension and magic-byte rejection, folder/symlink/shortcut rejection, all-or-nothing drops, no-peer and active-transfer rejection, ephemeral-copy cleanup, `receive_directory` validation (relative, traversal, file, unwritable), collision naming and exclusive-create races at the destination, hash mismatch at the destination, Wayland/X11 and multi-monitor geometry (Rust unit tests), and the edge state machine (vitest). §34/§41 fixtures use `.png`/`.pdf` instead of `.mp4`/`.exe`. Suites: `backend/tests/e2e/test_drop.py` (drop pipeline and push events against two real nodes), `unit/test_destination.py`, `unit/test_events.py`; frontend `edge/machine.test.ts`, `edge/controller.test.ts`, `input/inputs.test.ts`, `sync.test.ts`, `anim/motion.test.ts`; Rust `cargo test` for edge geometry (1.0x-2.0x scaling, multi-monitor) and the pointer tracker. Debug builds only: `HANDOFF_DEV_SCRIPT` (see `src-tauri/src/dev.rs`) replays inputs into the real UI for visual checks. Not automated: a real OS drag-and-drop (needs a human or a desktop-automation harness), and Windows behaviour.

## 1. Purpose

This document defines the testing strategy for HandOff Phase 1.

HandOff is a desktop file-transfer application involving:

- Tauri frontend
- Python backend
- SQLite database
- Local network communication
- TLS
- Device discovery
- Device trust
- Filesystem operations
- File validation
- SHA-256 integrity verification
- Transfer state management

Testing must therefore cover more than UI behavior.

The system must be tested at:

```text
Unit
  ↓
Integration
  ↓
Network
  ↓
Security
  ↓
End-to-End
  ↓
Cross-platform
```

---

# 2. Testing Philosophy

HandOff should be tested under the assumption that:

> Something will eventually break.

Tests should deliberately attempt to break:

- File validation
- Transfer state machines
- Network connections
- Device discovery
- Database operations
- Filesystem handling
- Security boundaries
- Recovery logic

A test is valuable when it proves that a failure is handled correctly.

---

# 3. Testing Stack

## Backend

Primary testing framework:

```text
pytest
```

Supporting tools may include:

```text
pytest-asyncio
pytest-cov
httpx
```

depending on the implementation.

---

# 4. Test Categories

The project shall contain:

```text
Unit Tests
Integration Tests
API Tests
Network Tests
Security Tests
Database Tests
Filesystem Tests
End-to-End Tests
Performance Tests
Regression Tests
```

---

# 5. Test Directory

Recommended structure:

```text
tests/
│
├── unit/
│   ├── test_devices.py
│   ├── test_files.py
│   ├── test_transfers.py
│   ├── test_settings.py
│   └── test_validation.py
│
├── integration/
│   ├── test_database.py
│   ├── test_transfer.py
│   ├── test_device_connection.py
│   └── test_filesystem.py
│
├── api/
│   ├── test_devices_api.py
│   ├── test_files_api.py
│   ├── test_transfer_api.py
│   └── test_settings_api.py
│
├── security/
│   ├── test_device_trust.py
│   ├── test_path_traversal.py
│   ├── test_file_validation.py
│   ├── test_integrity.py
│   └── test_network_security.py
│
├── e2e/
│   └── test_complete_transfer.py
│
├── fixtures/
│   ├── sample.txt
│   ├── sample.jpg
│   ├── sample.mp4
│   ├── sample.exe
│   └── duplicate/
│
└── conftest.py
```

The exact structure may evolve with implementation.

---

# 6. Test Environment

Tests must not use the user's real HandOff database or storage.

Every test should use an isolated environment.

Example:

```text
Test Application
│
├── temporary SQLite database
├── temporary file storage
├── temporary received storage
└── temporary transfer directory
```

Tests must clean up temporary resources after execution.

---

# 7. Database Testing

Database tests must verify:

- Database initialization
- Table creation
- Device creation
- Device updates
- File creation
- File deletion
- Transfer creation
- Transfer state updates
- Transfer-file relationships
- Settings persistence
- Audit log creation
- Foreign-key enforcement

---

# 8. Database Isolation

Each test should use an isolated database.

Tests must not depend on:

```text
previous test state
developer machine state
production database
```

Tests should be independently executable.

---

# 9. Database Integrity

Tests must verify that invalid relationships cannot be created.

Examples:

```text
Transfer → nonexistent device
TransferFile → nonexistent transfer
TransferFile → nonexistent file
```

These must fail safely.

---

# 10. File Management Tests

Test importing:

```text
.txt
.jpg
.mp4
.exe
```

Verify:

- File exists
- Metadata is stored
- Original filename is preserved
- File size is correct
- SHA-256 is correct
- Storage path is correct
- Database record is created

---

# 11. File Size Tests

The 50 MB limit must be tested around the boundary.

Required cases:

```text
0 bytes
1 byte
small file
49 MB
49.9 MB
exactly 50 MB
greater than 50 MB
```

The implementation must consistently enforce the defined Phase-1 limit.

The boundary behavior must be documented and tested explicitly.

---

# 12. File Extension Tests

Allowed:

```text
.txt
.jpg
.mp4
.exe
```

Rejected examples:

```text
.pdf
.docx
.zip
.png
.py
.sh
.bat
```

Case handling should also be tested:

```text
photo.JPG
PHOTO.jpg
video.MP4
program.EXE
```

The application should define and consistently apply its extension normalization rules.

---

# 13. Filename Tests

Test filenames containing:

```text
spaces
unicode characters
parentheses
hyphens
underscores
very long names
duplicate names
```

Also test invalid or dangerous filenames.

Examples:

```text
../../file.txt
..\..\file.txt
/etc/passwd
C:\Windows\System32\file.exe
```

---

# 14. Duplicate Filename Tests

Given:

```text
photo.jpg
```

if the destination already contains the file, the next received file should become:

```text
photo(1).jpg
```

If that exists:

```text
photo(2).jpg
```

Tests must verify that existing files are never silently overwritten.

---

# 15. SHA-256 Tests

For every managed file:

```text
SHA-256 must be deterministic.
```

Test:

```text
file
 ↓
hash
 ↓
same file
 ↓
same hash
```

Changing one byte must produce a different hash.

---

# 16. Transfer Integrity Tests

The receiver must verify the received file against the expected SHA-256.

Test:

```text
Correct file
    ↓
Matching hash
    ↓
Transfer succeeds
```

And:

```text
Modified/corrupted file
    ↓
Hash mismatch
    ↓
Transfer fails
```

The mismatch must create an audit log.

---

# 17. Device Tests

Test device registration and discovery.

Required cases:

```text
New device discovered
Known device discovered
Device reconnects
Device goes offline
Device comes back online
Unknown device attempts connection
```

---

# 18. Persistent Device Identity

A device must retain the same identity across application restarts.

Test:

```text
Start application
 ↓
Generate device identity
 ↓
Stop application
 ↓
Start application
 ↓
Verify same identity
```

A new identity must not be generated on every launch.

---

# 19. Device Trust Tests

Test:

```text
Trusted device
    ↓
Connection accepted
```

and:

```text
Unknown device
    ↓
Connection rejected
```

Also test that trust persists across application restart.

---

# 20. Device Offline Tests

When a device disappears from the network:

```text
status = offline
```

must be reflected promptly.

When it returns:

```text
status = available/connected
```

must be restored appropriately.

---

# 21. Receive Mode Tests

Test:

```text
Receive Mode OFF
+
Incoming transfer
=
Rejected
```

and:

```text
Receive Mode ON
+
Trusted device
+
Valid transfer
=
Accepted
```

Also verify persistence across application restart.

---

# 22. Transfer Job Tests

Creating a transfer must produce:

```text
transfer
transfer_files
audit_log
```

where applicable.

The transfer must have a unique ID.

---

# 23. Transfer State Machine Tests

Test valid transitions:

```text
created
 ↓
validating
 ↓
accepted
 ↓
transferring
 ↓
completed
```

Test failure paths:

```text
created → failed
validating → failed
transferring → failed
```

Invalid state transitions must be rejected.

For example:

```text
completed → transferring
```

must not be allowed.

---

# 24. One Active Transfer Test

Phase 1 permits one active transfer.

Test:

```text
Transfer A → active
Transfer B → request
```

The application must prevent two simultaneous active transfer jobs.

---

# 25. Multi-File Transfer Tests

When multiple files are selected:

```text
photo.jpg
video.mp4
notes.txt
```

they must belong to one transfer job.

Test:

```text
1 transfer
3 files
```

rather than:

```text
3 unrelated transfers
```

---

# 26. Partial Completion Tests

A multi-file transfer must be tested where:

```text
file 1 → success
file 2 → success
file 3 → failure
```

Expected:

```text
transfer.status = partially_completed
```

The history must clearly show which files succeeded and which failed.

---

# 27. Receiver Offline Tests

Test:

```text
Sender
  ↓
Transfer started
  ↓
Receiver disappears
```

Expected:

```text
Transfer → failed
```

The application must not remain indefinitely in:

```text
transferring
```

---

# 28. Sender Crash Tests

Simulate:

```text
Sender starts transfer
 ↓
Sender process terminates
```

The receiver must not remain permanently stuck.

Temporary data and transfer state must eventually be recoverable or marked failed.

---

# 29. Receiver Crash Tests

Simulate:

```text
Receiver accepts transfer
 ↓
Receiver terminates
```

After restart, the application must not incorrectly report the transfer as successfully completed.

---

# 30. Network Failure Tests

Test interruption during:

```text
connection
metadata exchange
file transfer
archive extraction
hash verification
```

The application must produce a deterministic failure state.

---

# 31. API Testing

Every public application API endpoint must have automated tests.

Test:

- Valid requests
- Missing parameters
- Invalid parameters
- Unknown device
- Invalid transfer ID
- Invalid file ID
- Oversized files
- Unsupported extensions
- Invalid state transitions
- Unauthorized remote requests

---

# 32. API Error Tests

Errors must return structured responses.

Example:

```json
{
  "error": {
    "code": "FILE_TOO_LARGE",
    "message": "File exceeds the 50 MB limit."
  }
}
```

Tests must verify:

- Error code
- HTTP/status equivalent
- Human-readable message
- No sensitive information leakage

---

# 33. Security Tests

Security tests must deliberately attack the application.

Required tests include:

```text
Unknown device
Invalid device identity
Invalid cryptographic identity
Path traversal
Malicious archive paths
Unsupported extension
Oversized file
Invalid SHA-256
Duplicate transfer ID
Duplicate filename
Transfer flooding
Malformed request
Invalid transfer state
```

---

# 34. Path Traversal Tests

The application must reject:

```text
../../secret.txt
../../../secret.txt
..\..\secret.txt
/etc/passwd
C:\Windows\System32\test.exe
```

Test archive entries as well as direct filenames.

---

# 35. Archive Extraction Tests

A malicious archive must not be able to extract outside the target directory.

Example:

```text
payload.zip
└── ../../outside.txt
```

Expected:

```text
Archive rejected
```

and:

```text
outside.txt
```

must not exist outside HandOff storage.

---

# 36. Transfer Replay Tests

A completed transfer must not be accepted again using the same transfer ID.

Test:

```text
Transfer ID: tr_001
Status: completed

Replay tr_001

→ Reject
```

---

# 37. Resource Exhaustion Tests

Test:

- Too many files
- Excessive metadata
- Multiple transfer requests
- Extremely long filenames
- Insufficient disk space
- Repeated invalid requests

The application must reject unsafe requests without crashing.

---

# 38. Filesystem Tests

Verify:

```text
File import
File storage
File receive
File rename on collision
File logical deletion
Temporary file creation
Temporary file cleanup
```

Tests must verify that paths remain inside HandOff-controlled directories.

---

# 39. Logical Deletion Tests

When a user deletes a file:

```text
File disappears from active gallery
```

but:

```text
Database history remains
Audit record remains
Transfer history remains
```

must be verified.

---

# 40. Audit Log Tests

Every important security and transfer event must create an audit record.

Examples:

```text
DEVICE_CONNECTED
DEVICE_OFFLINE
FILE_IMPORTED
FILE_DELETED
TRANSFER_CREATED
TRANSFER_STARTED
TRANSFER_COMPLETED
TRANSFER_FAILED
INVALID_HASH
INVALID_DEVICE
FILE_TOO_LARGE
INVALID_PATH
```

Tests should verify both event type and associated identifiers.

---

# 41. End-to-End Test

At least one automated end-to-end scenario must test the complete workflow.

```text
Application A starts
        ↓
Application B starts
        ↓
Device discovery
        ↓
A discovers B
        ↓
A connects to B
        ↓
B enables Receive Mode
        ↓
A imports file
        ↓
A selects file
        ↓
A sends file
        ↓
B accepts
        ↓
File transferred
        ↓
SHA-256 verified
        ↓
File stored
        ↓
History updated
        ↓
Audit log created
```

---

# 42. End-to-End Multi-File Test

A second E2E test must cover:

```text
Multiple files
      ↓
One transfer job
      ↓
Archive/package
      ↓
Transfer
      ↓
Extraction
      ↓
Individual file verification
      ↓
History
```

---

# 43. Real Network Testing

Automated local tests are not enough.

Before every release candidate, the application must be tested on at least:

```text
Windows Laptop A
        ↕
Linux Laptop B
```

over the same Wi-Fi network.

The test must verify:

- Discovery
- Connection
- TLS
- Receive Mode
- File transfer
- Hash verification
- History
- Offline detection

---

# 44. Cross-Platform Testing

Phase 1 supports:

```text
Windows x64
Linux x64
```

CI and release testing should cover both.

At minimum:

```text
Windows x64
Linux x64
```

---

# 45. CI Testing

GitHub Actions should execute automated tests on every pull request and push to protected branches.

Pipeline:

```text
Checkout
   ↓
Install dependencies
   ↓
Lint
   ↓
Type checking
   ↓
Unit tests
   ↓
Integration tests
   ↓
Security tests
   ↓
Coverage
```

Platform matrix:

```text
Windows x64
Linux x64
```

---

# 46. Test Coverage

Minimum backend coverage target:

```text
80%
```

Critical modules should target:

```text
90%+
```

Critical modules include:

- Transfer logic
- Security validation
- Device trust
- Filesystem handling
- File validation
- Database state transitions

Coverage percentage alone must not be treated as proof of correctness.

---

# 47. Coverage Exclusions

Coverage exclusions must be justified.

Do not exclude code simply because it is difficult to test.

Potential legitimate exclusions:

- Platform-specific bootstrap code
- Generated code
- Tauri framework internals
- Build scripts
- Static configuration

Every meaningful exclusion should be documented.

---

# 48. Frontend Testing

The Tauri frontend should have tests for critical UI logic.

At minimum:

- File selection
- Multi-selection
- Send button state
- Receive Mode toggle
- Device list
- Connection state
- Transfer progress
- Transfer completion
- Transfer failure
- Notifications
- History display

---

# 49. Send Button Rules

The UI must test that Send is enabled only when:

```text
At least one file selected
+
At least one connected device
```

Otherwise:

```text
Send = disabled
```

---

# 50. Device Selection Tests

If multiple devices are connected:

```text
User selects destination
```

The transfer must target only the selected device.

If only one device is connected, the UI should still clearly identify that destination.

---

# 51. Receive Toggle Tests

Test:

```text
OFF → ON
ON → OFF
```

and verify that the backend state changes accordingly.

The UI must not display ON while the backend is actually OFF.

---

# 52. Notification Tests

The application must generate appropriate notifications for:

```text
Transfer completed
Transfer failed
Transfer partially completed
Device connected
Device offline
Security rejection
```

---

# 53. Performance Tests

Phase 1 does not require enterprise benchmarking.

Basic measurements should still be collected for:

```text
Application startup
Device discovery
Device connection
Transfer initialization
Transfer throughput
```

These are baseline measurements rather than strict performance guarantees.

---

# 54. Startup Performance

Measure:

```text
Application launch
      ↓
Backend ready
      ↓
UI ready
```

The result should be recorded during release testing.

---

# 55. Transfer Performance

Measure transfer speed using representative files.

Example:

```text
10 MB
25 MB
50 MB
```

Test on the same Wi-Fi network.

Record:

```text
File size
Transfer duration
Average throughput
```

---

# 56. Regression Testing

Every bug fixed in HandOff should result in a regression test when practical.

Example:

```text
Bug:
photo.jpg overwritten photo.jpg

Fix:
collision naming

Regression test:
photo(1).jpg created
```

The test suite should prevent the same bug from returning.

---

# 57. Test Naming

Test names must describe behavior.

Good:

```python
def test_rejects_file_larger_than_limit():
```

Bad:

```python
def test_file():
```

Tests should clearly communicate expected behavior.

---

# 58. Deterministic Tests

Tests must avoid unnecessary dependencies on:

```text
real IP addresses
real user directories
real databases
real user files
Internet access
```

Where practical, dependencies should be mocked or isolated.

Real network tests remain necessary for final validation.

---

# 59. No Internet Dependency

Phase-1 automated tests must not require Internet connectivity.

HandOff's core functionality is LAN-based.

CI must be able to run without external service dependencies.

---

# 60. Test Data

Test fixtures should be small and reproducible.

Required fixture types:

```text
sample.txt
sample.jpg
sample.mp4
sample.exe
empty.txt
duplicate-name files
invalid extension file
```

Large generated fixtures may be created dynamically instead of committing large binaries to Git.

---

# 61. Destructive Testing

The test suite should deliberately simulate:

```text
network failure
process termination
invalid data
corrupted files
disk limitations
duplicate requests
invalid identities
malicious paths
```

The objective is to confirm that HandOff fails safely.

---

# 62. Test Execution Commands

The project should provide simple commands such as:

```bash
pytest
```

For coverage:

```bash
pytest --cov
```

For a specific category:

```bash
pytest tests/security/
```

For integration:

```bash
pytest tests/integration/
```

The exact commands may be adjusted according to the final Python tooling.

---

# 63. Pre-Commit Testing

Before committing substantial changes, Claude Code should run at minimum:

```text
Lint
Type checks
Relevant unit tests
Relevant integration tests
```

Before declaring a feature complete:

```text
Full test suite
Security tests
Coverage
```

must be executed.

---

# 64. Pull Request Requirements

A pull request must not be considered complete if:

- Tests fail
- Security tests fail
- Existing tests regress
- Coverage drops significantly without justification
- New critical functionality has no tests

---

# 65. Definition of Done

A Phase-1 feature is considered tested when:

```text
Implementation
    ↓
Unit tests
    ↓
Integration tests
    ↓
Security tests
    ↓
Relevant E2E test
    ↓
Cross-platform validation
    ↓
Documentation updated
```

---

# 66. Release Test Checklist

Before a Phase-1 release:

```text
[ ] Unit tests pass
[ ] Integration tests pass
[ ] API tests pass
[ ] Security tests pass
[ ] E2E transfer passes
[ ] Multi-file transfer passes
[ ] Partial transfer tested
[ ] 50 MB boundary tested
[ ] SHA-256 verification tested
[ ] Path traversal tested
[ ] Duplicate filename tested
[ ] Unknown device rejected
[ ] Receive Mode tested
[ ] Offline detection tested
[ ] Windows x64 tested
[ ] Linux x64 tested
[ ] Real LAN transfer tested
[ ] Temporary files cleaned
[ ] Audit logs verified
[ ] Transfer history verified
[ ] No secrets committed
```

---

# 67. Testing Rules for Claude Code

Claude Code must follow these rules:

1. Do not declare a feature complete without tests.
2. Test failure conditions, not only successful conditions.
3. Test security boundaries explicitly.
4. Never use the production database in tests.
5. Never use real user files in automated tests.
6. Keep tests deterministic.
7. Add regression tests for meaningful bugs.
8. Test the 50 MB boundary.
9. Test SHA-256 verification.
10. Test path traversal.
11. Test unknown-device rejection.
12. Test transfer state transitions.
13. Test partial transfers.
14. Test device offline behavior.
15. Test duplicate filenames.
16. Test application restart behavior.
17. Test both Windows x64 and Linux x64.
18. Run the full suite before release.
19. Do not reduce security testing to increase coverage numbers.
20. Treat a passing UI test as insufficient proof that the transfer system works.

---

# 68. Final Testing Principle

The core success criterion for HandOff is not:

> "The UI works."

It is:

> **A trusted device can reliably transfer valid files to another trusted device over the LAN, while invalid, corrupted, oversized, unauthorized, or malicious inputs are rejected safely and the complete operation is recorded correctly.**

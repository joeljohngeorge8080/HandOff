# Database Design

> **Phase 2 amendment (ADR-055).** No schema change. Settings: new key `receive_directory` (absolute path; unset means the OS Desktop); `receive_mode` is **obsolete** — left in existing databases, ignored, never deleted. Received files no longer create `files` rows; their `transfer_files.file_id` is NULL and `original_name` keeps the sender's file name (the contract key); the name actually written, after any `(1)` suffix, is in the `FILE_RECEIVED` audit entry and the reply's `saved_as`. Dropped files are imported as `files(source='imported')` and logically deleted (`deleted_at`) when their transfer ends. New audit event `RECEIVE_DIRECTORY_CHANGED` replaces `RECEIVE_MODE_CHANGED` (the old events stay valid in history).

## 1. Purpose

This document defines the persistent data model for HandOff Phase 1.

HandOff uses:

- **SQLite** for structured persistent data
- **SQLAlchemy 2.x** as the Python database layer
- Application-managed filesystem storage for binary files

The database stores metadata and application state.

Binary files must **not** be stored directly inside SQLite.

---

# 2. Database Architecture

```text
HandOff Application
│
├── SQLite
│   └── handoff.db
│
└── Application Storage
    ├── files/
    ├── received/
    ├── transfers/
    └── temp/
```

Conceptually:

```text
SQLite
  │
  ├── devices
  ├── files
  ├── transfers
  ├── transfer_files
  ├── settings
  └── audit_logs

Filesystem
  │
  ├── Imported files
  ├── Received files
  └── Temporary transfer archives
```

---

# 3. Database Technology

## 3.1 SQLite

SQLite is the database engine for Phase 1.

Reasons:

- No database server required
- Works offline
- Cross-platform
- Easy application packaging
- Suitable for the MVP workload
- Simple backup and recovery
- Low operational complexity

HandOff does not require PostgreSQL, MySQL, or another external database server for Phase 1.

---

# 4. ORM

HandOff shall use:

```text
SQLAlchemy 2.x
```

SQLAlchemy provides:

- Database abstraction
- Model definitions
- Transactions
- Query construction
- Relationship management
- Type-safe database interaction
- SQLite integration

The application must not scatter raw SQL throughout the codebase.

Raw SQL may be used only when there is a clear technical reason.

---

# 5. Database Location

The database shall live inside HandOff's application data directory.

The exact operating-system path should be determined using the platform's standard application-data directory.

Conceptually:

### Windows

```text
%APPDATA%/HandOff/
```

### Linux

```text
~/.local/share/HandOff/
```

The application must not assume a fixed absolute path.

---

# 6. Storage Layout

The application data directory should have a structure similar to:

```text
HandOff/
│
├── handoff.db
│
├── files/
│   └── ...
│
├── received/
│   └── ...
│
├── transfers/
│   └── ...
│
└── temp/
    └── ...
```

The exact internal directory structure may evolve as implementation progresses.

---

# 7. Storage Principles

The following rules are mandatory.

### Database

Stores:

- File metadata
- Device metadata
- Transfer metadata
- Settings
- Audit information

### Filesystem

Stores:

- Actual imported files
- Actual received files
- Temporary transfer archives

### SQLite must never store

- Images as BLOBs
- Videos as BLOBs
- Executables as BLOBs
- ZIP archives as BLOBs

---

# 8. Database Tables

Phase 1 contains six tables:

```text
devices
files
transfers
transfer_files
settings
audit_logs
```

Relationship overview:

```text
                 ┌──────────────┐
                 │   devices    │
                 └──────┬───────┘
                        │
                        │
              ┌─────────▼─────────┐
              │     transfers     │
              └─────────┬─────────┘
                        │
                        │
              ┌─────────▼─────────┐
              │  transfer_files   │
              └─────────┬─────────┘
                        │
                        │
                 ┌──────▼──────┐
                 │    files    │
                 └─────────────┘

                 ┌─────────────┐
                 │  settings   │
                 └─────────────┘

                 ┌─────────────┐
                 │ audit_logs  │
                 └─────────────┘
```

---

# 9. Devices Table

The `devices` table stores known HandOff devices.

A device remains in the database even when it is offline.

## Schema

```text
devices
```

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | INTEGER | PK | Internal database ID |
| `device_id` | TEXT | UNIQUE, NOT NULL | Persistent HandOff device identity |
| `device_name` | TEXT | NOT NULL | Device hostname/name |
| `platform` | TEXT | NOT NULL | Windows/Linux |
| `last_ip` | TEXT | NULL | Most recently known IP |
| `port` | INTEGER | NULL | Last known API port |
| `public_key` | TEXT | NULL | Peer's public Ed25519 key (base64). The private key is never stored here |
| `is_trusted` | INTEGER | NOT NULL, DEFAULT 0 | 1 when the user connected/trusted this device |
| `status` | TEXT | NOT NULL | Current availability |
| `first_seen_at` | DATETIME | NOT NULL | First discovery |
| `last_seen_at` | DATETIME | NOT NULL | Last discovery |
| `created_at` | DATETIME | NOT NULL | Record creation |
| `updated_at` | DATETIME | NOT NULL | Last update |

---

# 10. Device Identity

`device_id` is the permanent identity of a HandOff installation.

Example:

```text
7e7d8c2a-5e9e-4e1c-9a7d-9a1c4e1f7a31
```

The IP address must **never** be used as the device's identity.

The IP address can change.

The device ID must remain stable.

---

# 11. Device Status

Possible values:

```text
available
connecting
connected
offline
```

The application may internally use additional transient states if required, but the persisted state should remain simple.

---

# 12. Device Naming

Phase 1 does not provide a user-facing device-name editing feature.

The device name should be automatically derived from the operating system or generated during initialization.

Example:

```text
Joel-Laptop
```

The user cannot manually rename it through the Phase-1 UI.

---

# 13. Files Table

The `files` table represents files managed by HandOff.

This includes both:

```text
imported
received
```

files.

## Schema

```text
files
```

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | TEXT | PK | Application file UUID |
| `original_name` | TEXT | NOT NULL | Display filename; made unique among active files as `name(1).ext` (ADR-020, ADR-053). The sender's true name is kept in `transfer_files.original_name` |
| `stored_name` | TEXT | NOT NULL | Actual filesystem name |
| `extension` | TEXT | NOT NULL | File extension |
| `mime_type` | TEXT | NULL | Detected MIME type |
| `size_bytes` | INTEGER | NOT NULL | File size |
| `sha256` | TEXT | NOT NULL | SHA-256 hash |
| `source` | TEXT | NOT NULL | `imported` or `received` |
| `storage_path` | TEXT | NOT NULL | Relative application storage path |
| `created_at` | DATETIME | NOT NULL | Import/receive timestamp |
| `updated_at` | DATETIME | NOT NULL | Last metadata update |
| `deleted_at` | DATETIME | NULL | Logical deletion timestamp |

---

# 14. Original Filename

The database must preserve the original filename.

Example:

```text
original_name:
vacation.jpg
```

The filesystem may use an internal storage name.

The UI should display:

```text
vacation.jpg
```

rather than exposing implementation-specific storage names.

---

# 15. File Storage Names

Files should use an internal unique storage identifier to prevent collisions.

Example:

```text
files/
└── 8f0d1c32-1f3a-4b11-a123-123456789abc
```

The database retains:

```text
original_name = photo.jpg
```

This separates:

```text
User-visible filename
```

from:

```text
Filesystem storage identity
```

---

# 16. Why Storage Names Are Separate

Two files may have the same original name:

```text
photo.jpg
photo.jpg
```

The application must still be able to store both safely.

Therefore:

```text
original_name
```

and:

```text
stored_name
```

must not be treated as the same field.

---

# 17. File Source

The `source` field identifies how the file entered HandOff.

Allowed values:

```text
imported
received
```

Example:

```text
photo.jpg → imported
video.mp4 → received
```

---

# 18. File Hash

Every managed file shall have a SHA-256 hash.

Example:

```text
sha256:
9f86d081884c7d659a2feaa0c55ad015...
```

The hash is used for:

- Integrity verification
- Debugging
- Transfer validation
- File identification
- Future deduplication capabilities

Phase 1 does not perform automatic deduplication.

---

# 19. Transfer Table

The `transfers` table represents transfer jobs.

Phase 1 allows:

```text
1 active transfer job
```

at a time.

## Schema

```text
transfers
```

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | TEXT | PK | Transfer UUID |
| `direction` | TEXT | NOT NULL | `sent` or `received` |
| `source_device_id` | TEXT | FK, NULL | Sending device. NULL means this device (the local device has no `devices` row) |
| `destination_device_id` | TEXT | FK, NULL | Receiving device. NULL means this device |
| `status` | TEXT | NOT NULL | Transfer state |
| `file_count` | INTEGER | NOT NULL | Number of files |
| `total_size_bytes` | INTEGER | NOT NULL | Total original file size |
| `archive_size_bytes` | INTEGER | NULL | ZIP size |
| `bytes_transferred` | INTEGER | DEFAULT 0 | Current progress |
| `error_code` | TEXT | NULL | Failure code |
| `error_message` | TEXT | NULL | Human-readable error |
| `created_at` | DATETIME | NOT NULL | Creation timestamp |
| `started_at` | DATETIME | NULL | Transfer start |
| `completed_at` | DATETIME | NULL | Completion timestamp |

---

# 20. Transfer ID

Each transfer must have a globally unique identifier.

Example:

```text
tr_01JABC789
```

The transfer ID is used by:

- API
- Logs
- History
- Debugging
- UI
- Error reporting

---

# 21. Transfer Direction

Allowed values:

```text
sent
received
```

From the local device's perspective.

Example:

```text
Laptop A → Laptop B

Laptop A:
direction = sent

Laptop B:
direction = received
```

---

# 22. Transfer Status

Allowed primary states:

```text
created
validating
accepted
transferring
completed
failed
partially_completed
```

The state machine is:

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

Failure:

```text
created
   ↓
failed
```

or:

```text
transferring
   ↓
failed
```

Partial result:

```text
transferring
   ↓
partially_completed
```

---

# 23. Transfer File Table

A transfer job must be associated with the files involved in that job.

The `transfer_files` table provides this relationship.

## Schema

```text
transfer_files
```

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | INTEGER | PK | Internal ID |
| `transfer_id` | TEXT | FK, NOT NULL | Transfer job |
| `file_id` | TEXT | FK, NULL | File. NULL when a received file failed validation and never became a managed file |
| `original_name` | TEXT | NOT NULL | Name at transfer time |
| `size_bytes` | INTEGER | NOT NULL | File size |
| `sha256` | TEXT | NOT NULL | Hash at transfer time |
| `status` | TEXT | NOT NULL | File transfer status |
| `failure_code` | TEXT | NULL | Failure code |
| `failure_message` | TEXT | NULL | Failure message |
| `created_at` | DATETIME | NOT NULL | Record creation |
| `completed_at` | DATETIME | NULL | File completion |

---

# 24. Transfer File Status

Allowed values:

```text
pending
transferring
completed
failed
```

This enables the application to determine whether a transfer was:

```text
completed
```

or:

```text
partially_completed
```

---

# 25. Transfer History Persistence

Transfer records must remain in the database even after the actual file is deleted from the gallery.

Example:

```text
Transfer:
photo.jpg
Status:
completed
```

The user later deletes:

```text
photo.jpg
```

The transfer history remains:

```text
✓ photo.jpg
Sent → Aaron-Laptop
```

The database must therefore avoid cascading deletion from a file record to its historical transfer information.

---

# 26. File Deletion Model

HandOff uses **logical deletion**.

When the user deletes a file:

```text
deleted_at
```

is populated.

The database record is not immediately destroyed.

Example:

```text
deleted_at:
2026-10-01T11:30:00Z
```

The actual filesystem file may be moved to an internal trash/recovery location or otherwise removed from active storage according to the implementation.

The database record remains available for audit/history purposes.

---

# 27. Audit Logs

Phase 1 includes an `audit_logs` table.

This is separate from transfer history.

Transfer history answers:

> What happened to a transfer?

Audit logs answer:

> What happened inside the application?

---

# 28. Audit Logs Schema

```text
audit_logs
```

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | INTEGER | PK | Log ID |
| `event_type` | TEXT | NOT NULL | Event category |
| `actor` | TEXT | NULL | Local application/device |
| `device_id` | TEXT | NULL | Related device |
| `file_id` | TEXT | NULL | Related file |
| `transfer_id` | TEXT | NULL | Related transfer |
| `message` | TEXT | NOT NULL | Human-readable description |
| `metadata` | TEXT | NULL | JSON metadata |
| `created_at` | DATETIME | NOT NULL | Event timestamp |

---

# 29. Audit Event Examples

Examples include:

```text
DEVICE_DISCOVERED
DEVICE_CONNECTED
DEVICE_OFFLINE

FILE_IMPORTED
FILE_DELETED
FILE_RECEIVED

TRANSFER_CREATED
TRANSFER_STARTED
TRANSFER_COMPLETED
TRANSFER_FAILED

RECEIVE_MODE_CHANGED

APPLICATION_STARTED
APPLICATION_STOPPED
```

---

# 30. Audit Log Metadata

The `metadata` field may contain JSON.

Example:

```json
{
  "file_name": "photo.jpg",
  "size_bytes": 4823912
}
```

Another example:

```json
{
  "old_value": false,
  "new_value": true
}
```

---

# 31. Settings Table

Application configuration that must persist between launches is stored in `settings`.

## Schema

```text
settings
```

| Column | Type | Constraints | Description |
|---|---|---|---|
| `key` | TEXT | PK | Setting identifier |
| `value` | TEXT | NOT NULL | Serialized value |
| `value_type` | TEXT | NOT NULL | Value type |
| `updated_at` | DATETIME | NOT NULL | Last update |

---

# 32. Settings

Phase-1 settings include at minimum:

```text
receive_mode
history_retention
device_name
schema_version
```

`schema_version` drives the in-app upgrade routine (see Schema Changes).

Storage locations may also be represented through settings if configurable in the implementation.

---

# 33. Receive Mode Persistence

Receive Mode must persist across application restarts.

Example:

```text
Before restart:
Receive Mode = ON

Application restarts.

After restart:
Receive Mode = ON
```

The implementation should ensure this behavior is deliberate rather than accidental.

---

# 34. History Retention

Transfer history retention is user configurable.

The database must support a cleanup process that removes historical records older than the configured retention period.

However, cleanup must not accidentally remove required audit information unless the retention policy explicitly applies to audit logs.

---

# 35. Recommended History Cleanup

The default implementation should use a configurable retention period.

Example:

```text
30 days
90 days
1 year
Forever
```

The exact UI choices may be finalized during implementation.

The cleanup operation should run periodically rather than during every database query.

---

# 36. Audit Log Retention

Audit logs should use a longer retention period than normal transfer history.

The implementation may retain audit logs indefinitely during Phase 1 unless storage growth becomes a practical issue.

Audit logs should not be deleted simply because a file was deleted.

---

# 37. Foreign Keys

SQLite foreign-key enforcement must be enabled.

Conceptually:

```sql
PRAGMA foreign_keys = ON;
```

This prevents invalid references between:

```text
devices
transfers
transfer_files
files
```

---

# 38. Referential Integrity

Relationships must remain valid.

Example:

```text
transfer
   │
   └── transfer_files
           │
           └── files
```

Deleting a file must not destroy historical transfer records.

Therefore, historical relationships must be designed to survive logical file deletion.

---

# 39. Indexes

The following fields should be indexed.

### Devices

```text
device_id
status
last_seen_at
```

### Files

```text
source
created_at
deleted_at
sha256
```

### Transfers

```text
status
direction
created_at
source_device_id
destination_device_id
```

### Transfer Files

```text
transfer_id
file_id
status
```

### One active transfer

A partial unique index (`uq_transfers_one_active`) allows at most one transfer whose status is not `completed`, `failed` or `partially_completed`. The database itself therefore enforces the Phase-1 one-active-transfer rule (ADR-015).

### Audit Logs

```text
event_type
device_id
file_id
transfer_id
created_at
```

---

# 40. Unique Constraints

The following values must be unique where applicable:

```text
devices.device_id
files.id
transfers.id
```

The original filename must **not** be globally unique.

Multiple files may have:

```text
photo.jpg
```

---

# 41. Transactions

Database modifications must use transactions.

Example:

```text
Create transfer
      │
      ├── transfer record
      ├── transfer_files records
      └── audit log
```

These operations should be committed atomically when they represent one logical state change.

---

# 42. Transfer Transaction Model

The transfer process involves both:

```text
Filesystem
```

and:

```text
Database
```

These cannot be handled as one true atomic transaction.

Therefore, the application must use explicit state transitions and recovery logic.

Example:

```text
Database:
TRANSFER_CREATED

Filesystem:
Create temporary archive

Database:
TRANSFER_STARTED

Network:
Transfer archive

Filesystem:
Extract

Database:
TRANSFER_COMPLETED
```

If a filesystem operation fails, the transfer must be marked:

```text
FAILED
```

rather than leaving the database in an incorrect success state.

---

# 43. Temporary Transfer Data

Temporary archives must not be treated as permanent files.

Example:

```text
transfers/
├── tr_001/
│   └── payload.zip
```

After successful completion:

```text
payload.zip
```

must be removed.

If a transfer fails, temporary data must also be cleaned up.

The application may retain temporary data temporarily for recovery if necessary, but it must not accumulate indefinitely.

---

# 44. Active Transfer Constraint

Phase 1 allows only:

```text
1 active transfer job
```

at any given time.

Therefore, the application must prevent:

```text
Transfer A → active
Transfer B → active
```

simultaneously.

Instead:

```text
Transfer A → active
Transfer B → waiting
```

or the UI should prevent creation of another transfer until the current transfer completes.

The Phase-1 implementation should prefer preventing the second active job.

---

# 45. Database Concurrency

SQLite supports concurrent reads but has limitations around concurrent writes.

The Python backend should:

- Keep transactions short
- Avoid long-running database transactions
- Avoid blocking the UI
- Serialize write-heavy operations where appropriate
- Use a single database access layer

---

# 46. Database Access Layer

Application code should not directly access SQLAlchemy models everywhere.

Use a dedicated database/repository layer.

Conceptually:

```text
Application Services
        │
        ▼
Repositories
        │
        ▼
SQLAlchemy
        │
        ▼
SQLite
```

Example:

```text
DeviceRepository
FileRepository
TransferRepository
SettingsRepository
AuditRepository
```

---

# 47. Schema Changes

Phase 1 deliberately does **not** use Alembic.

Claude Code may modify the schema directly during development.

However, every schema change must:

1. Update SQLAlchemy models.
2. Update this `DATABASE.md`.
3. Update affected tests.
4. Handle existing local databases appropriately.
5. Avoid silent data loss.

For major schema changes, Claude Code must create a migration or upgrade routine inside the application's database initialization layer even though Alembic is not used.

---

# 48. Database Initialization

On application startup:

```text
Application starts
      ↓
Determine application data directory
      ↓
Open SQLite database
      ↓
Enable foreign keys
      ↓
Initialize schema
      ↓
Validate required tables
      ↓
Start application
```

If the database does not exist, it must be created automatically.

---

# 49. Database Recovery

If the database cannot be opened:

```text
Application
     ↓
Database initialization fails
     ↓
Log detailed error
     ↓
Show user-friendly error
```

The application must not silently create a second database somewhere else.

---

# 50. Backup Consideration

Phase 1 does not require automatic database backups.

However, because:

```text
handoff.db
```

contains:

- Device information
- Transfer history
- Settings
- Audit logs

the database should remain independently recoverable.

A future backup/export feature may be added.

---

# 51. Example Database State

A normal installation might contain:

```text
devices
────────────────────────────
device_001 | Joel-Laptop | connected
device_002 | Aaron-Laptop | offline


files
────────────────────────────
file_001 | photo.jpg | imported
file_002 | video.mp4 | received


transfers
────────────────────────────
tr_001 | sent     | completed
tr_002 | received | completed
tr_003 | sent     | failed


transfer_files
────────────────────────────
tr_001 | file_001 | completed
tr_001 | file_002 | completed


settings
────────────────────────────
receive_mode      | true
history_retention | 90


audit_logs
────────────────────────────
DEVICE_CONNECTED
FILE_IMPORTED
TRANSFER_CREATED
TRANSFER_COMPLETED
```

---

# 52. Database Design Rules for Claude Code

Claude Code must follow these rules:

1. Use SQLAlchemy 2.x.
2. Use SQLite.
3. Keep binary data outside the database.
4. Use UUID-based application IDs.
5. Preserve original filenames.
6. Store SHA-256 hashes.
7. Keep transfer history after file deletion.
8. Use logical deletion for managed files.
9. Maintain audit logs.
10. Enforce SQLite foreign keys.
11. Use transactions for logical state changes.
12. Allow only one active transfer in Phase 1.
13. Do not introduce Alembic.
14. Do not silently destroy existing data during schema changes.
15. Update this document when the schema intentionally changes.
16. Add tests for every schema change.
17. Never let UI code directly manipulate the database.

---

# 53. Future Compatibility

The Phase-1 database should remain extensible for future functionality.

Potential future entities include:

```text
gesture_events
cv_sessions
device_capabilities
transfer_targets
gesture_actions
```

These are **not Phase-1 tables**.

They must not be added merely because future functionality is planned.

The MVP database should remain small and focused.

---

# 54. Final Data Model

The Phase-1 data model is:

```text
                    ┌──────────────┐
                    │   DEVICES    │
                    │              │
                    │ device_id PK │
                    │ device_name  │
                    │ status       │
                    └───────┬──────┘
                            │
                            │
                  ┌─────────▼─────────┐
                  │     TRANSFERS     │
                  │                   │
                  │ transfer_id PK    │
                  │ direction         │
                  │ status            │
                  │ source_device     │
                  │ destination       │
                  └─────────┬─────────┘
                            │
                            │
                  ┌─────────▼─────────┐
                  │  TRANSFER_FILES   │
                  │                   │
                  │ transfer_id       │
                  │ file_id            │
                  │ status             │
                  │ sha256             │
                  └─────────┬─────────┘
                            │
                            │
                    ┌───────▼──────┐
                    │    FILES     │
                    │              │
                    │ file_id PK   │
                    │ original_name│
                    │ storage_path │
                    │ sha256       │
                    │ source       │
                    │ deleted_at   │
                    └──────────────┘


              ┌──────────────────┐
              │     SETTINGS     │
              │                  │
              │ key              │
              │ value            │
              └──────────────────┘


              ┌──────────────────┐
              │   AUDIT_LOGS     │
              │                  │
              │ event_type       │
              │ device_id        │
              │ file_id          │
              │ transfer_id      │
              │ metadata         │
              └──────────────────┘
```

This is the authoritative Phase-1 database design.

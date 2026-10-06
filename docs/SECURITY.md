# Security Requirements

> **Phase 2 amendment (ADR-055).** §19 (Receive Mode) is removed: the trust, identity and connected-peer checks are the only gate. §21/§33: allowed types are `.txt .jpg .jpeg .png .pdf`; `.exe` and `.mp4` are rejected, and files with a PE (`MZ`) or ELF header are rejected even when renamed, on sender and receiver. §29/§55 #7 now read: received files are written only into the receiver's own validated `receive_directory` (absolute, existing, writable; default OS Desktop), never to a path supplied by the network. Names are reserved with exclusive create (`name(1).ext`), nothing is overwritten, and each file's SHA-256 is re-verified while it is copied to the destination. New audit event: `RECEIVE_DIRECTORY_CHANGED`. Received files outside app-data are not removed by uninstall.

## 1. Purpose

This document defines the security model for HandOff Phase 1.

HandOff is a desktop application that transfers files between trusted devices on the same local network.

Phase 1 prioritizes:

- Fast device connection
- Simple LAN-based operation
- Trusted devices
- Encrypted communication
- File integrity
- Safe filesystem handling
- Clear security logging
- Minimal user friction

HandOff does **not** implement user accounts or cloud authentication in Phase 1.

---

# 2. Security Model

Phase 1 uses a:

> **Trusted LAN Security Model**

The assumption is:

```text
Same LAN
    ↓
User-controlled environment
    ↓
HandOff devices are trusted
```

However, being on the same network does **not** automatically mean every device is authorized.

Only devices that have been explicitly connected/trusted through HandOff may participate in file transfers.

---

# 3. Security Level

Phase 1 uses:

```text
Trusted LAN
+
Permanent Device Identity
+
Cryptographic Device Identity
+
TLS Encryption
+
File Integrity Verification
+
Filesystem Validation
```

It does not implement a full zero-trust architecture.

---

# 4. Threat Model

HandOff Phase 1 considers the following threats.

### Threats considered

- Unknown device attempting to connect
- Device identity spoofing
- Man-in-the-middle attacks
- File corruption during transfer
- Malicious filenames
- Path traversal
- Oversized files
- Unsupported file types
- Transfer flooding
- Invalid transfer requests
- Tampered transfer metadata
- Unauthorized access to received files
- Network packet interception

### Threats explicitly outside Phase 1

- Malware analysis
- Antivirus scanning
- Cloud account compromise
- Compromised operating system
- Root/admin-level attacker on the host
- Physical access attacks
- Enterprise-grade zero-trust networking

---

# 5. Device Trust

A HandOff device becomes trusted when the user explicitly connects it through the application.

Example:

```text
Available Devices

┌────────────────────────────┐
│ 🟢 Aaron-Laptop            │
│    192.168.1.15            │
│                    [Connect]│
└────────────────────────────┘
```

After successful connection:

```text
Aaron-Laptop
Status: Connected
Trusted: Yes
```

The device remains trusted across application restarts.

---

# 6. Device Identity

Every HandOff installation has a permanent:

```text
device_id
```

The ID must not be based on:

- IP address
- MAC address
- Hostname
- Network interface

because these values can change or be spoofed.

Example:

```text
device_id:
7e7d8c2a-5e9e-4e1c-9a7d-9a1c4e1f7a31
```

---

# 7. Cryptographic Device Identity

Each HandOff installation should generate a cryptographic key pair.

Conceptually:

```text
Private Key
    +
Public Key
```

The private key never leaves the local machine.

The public key can be shared with trusted devices.

This provides stronger device identity than relying only on a UUID.

---

# 8. Private Key Protection

The private key must:

- Remain on the local machine
- Never be transmitted
- Never be exposed through the API
- Never be written to logs
- Never be included in transfer payloads

The private key should be stored in the application's protected storage directory with restrictive filesystem permissions.

Where practical, platform-specific secure storage may be used in future versions.

---

# 9. Device Authentication

When two devices connect:

```text
Laptop A
    │
    │ connection
    ▼
Laptop B
```

HandOff verifies the remote device's cryptographic identity.

The goal is to ensure:

> The device claiming to be device X is actually the trusted device X.

---

# 10. Pairing Philosophy

Phase 1 deliberately avoids complicated pairing workflows.

The user experience should remain:

```text
Find Device
    ↓
Click Connect
    ↓
Connection established
    ↓
Device trusted
```

There should be no:

- Password entry
- OTP
- Account login
- Cloud authentication
- Manual certificate installation

The cryptographic exchange happens behind the scenes.

---

# 11. Persistent Trust

Once a device has been successfully connected and trusted:

```text
Trusted Device
```

the trust relationship persists across application restarts.

The user should not need to reconnect the device every time HandOff launches.

---

# 12. Removing Trust

Phase 1 does not require a dedicated disconnect/untrust button.

If trust-management functionality becomes necessary later, it can be added as a separate feature.

The absence of a disconnect button must not prevent the application from detecting that a device is offline.

---

# 13. Network Discovery

The implementation should use **mDNS/service discovery** for local-device discovery.

The objective is:

```text
HandOff starts
      ↓
Advertise HandOff service
      ↓
Discover other HandOff devices
      ↓
Display devices in UI
```

The implementation should avoid requiring users to manually enter IP addresses.

---

# 14. Network Scope

HandOff Phase 1 operates on the local network.

Expected environment:

```text
Laptop A ─┐
Laptop B ─┼── Wi-Fi/LAN
Laptop C ─┘
```

The application is not designed for Internet-based file transfer.

---

# 15. API Binding

The local service should bind to the appropriate LAN interface required for device-to-device communication.

It must **not unnecessarily expose administrative interfaces to all network interfaces**.

Development/debug interfaces must not be exposed in production builds.

The implementation should determine the appropriate bind address dynamically.

---

# 16. Transport Encryption

All device-to-device communication must use encrypted transport.

Recommended mechanism:

```text
TLS
```

Conceptually:

```text
Laptop A
   │
   │ encrypted TLS connection
   ▼
Laptop B
```

Plain HTTP or unencrypted raw TCP file transfers must not be used for production transfer traffic.

---

# 17. TLS Requirements

TLS must protect:

- Device connection
- Device metadata
- Transfer requests
- Transfer metadata
- File data
- Transfer status

No sensitive transfer information should be transmitted over plaintext network protocols.

---

# 18. Certificate/Identity Validation

Because HandOff uses trusted devices rather than public Internet PKI, normal public certificate authorities are unnecessary.

The application should use device identity/public-key verification to establish trust.

The exact certificate strategy may be implemented using locally generated credentials appropriate for the chosen TLS library.

The important requirement is:

```text
Encryption alone is insufficient.
Identity must also be verified.
```

---

# 18.1 Identity Verification Mechanism

Phase 1 verifies identity at two layers (ADR-053):

- **Server identity:** the client pins the server's TLS public key. On first connect it is compared with the fingerprint advertised in the mDNS record; afterwards with the stored `devices.public_key`.
- **Client identity:** every request carries an Ed25519 signature (see `API.md` §43.1) with a timestamp window and nonce replay rejection.

Receive Mode is changed only locally by the user. A peer can never change it.

---

# 18.2 Known Limitations of the Phase-1 Trust Model

These follow directly from the product requirements and are accepted for the MVP:

- **Trust on first use.** On the first connection to a device, the expected identity is the key advertised over mDNS, which is not authenticated. An attacker on the same LAN who answers before the real device could be trusted by mistake. After the first connection the key is pinned and any change is refused.
- **Connecting trusts both ways.** The user who clicks Connect makes themselves trusted on the other device, without an approval prompt there (FR-011). The protection is that Receive Mode defaults to **OFF**, and a device only accepts files while the user has turned it on.
- **No revocation.** There is no untrust/disconnect UI in Phase 1 (§12). A trusted device stays trusted.
- **Clock skew.** Signed requests are accepted within ±60 seconds of the receiver's clock; devices with badly wrong clocks cannot talk.
- **Stalled senders.** A sender that opens an upload and then goes silent is failed after 60 seconds.

Stronger pairing (for example a short confirmation code) is the intended Phase-2+ hardening and needs an ADR (ADR-045).

---

# 19. Receive Mode Security

Receive Mode does not mean:

> Accept files from anyone on the LAN.

It means:

> Accept files from trusted HandOff devices.

Therefore:

```text
Receive Mode = ON
+
Trusted Device
=
Accept transfer
```

while:

```text
Receive Mode = ON
+
Unknown Device
=
Reject transfer
```

---

# 20. Unknown Devices

An unknown HandOff device must not be allowed to send files to the local machine.

The application should reject the request and record the event.

Example audit event:

```text
INVALID_DEVICE
```

---

# 21. File Type Restrictions

Phase 1 supports only:

```text
.txt
.jpg
.mp4
.exe
```

The receiver must validate the file extension before accepting the transfer.

Unsupported files must be rejected.

Example:

```text
document.pdf
```

Result:

```text
Rejected:
UNSUPPORTED_FILE
```

---

# 22. File Size Restriction

Every individual file must be:

```text
<= 50 MB
```

Files exceeding the Phase-1 limit must be rejected.

Example:

```text
video.mp4
Size: 72 MB

→ REJECTED
```

The limit must be enforced on both:

```text
Sender
Receiver
```

The receiver must not trust the sender's declared file size.

---

# 23. Maximum Files Per Transfer

Phase 1 supports multiple selected files.

However, a transfer job must have a configured maximum number of files.

The exact maximum should be defined as a configurable application constant.

Example:

```text
MAX_FILES_PER_TRANSFER
```

This prevents an attacker or malfunctioning client from creating an excessively large transfer request.

---

# 24. Transfer Packaging

When multiple files are selected:

```text
photo1.jpg
photo2.jpg
video.mp4
```

they may be packaged into a single transfer archive.

Conceptually:

```text
Transfer Job
    │
    └── payload.zip
          ├── photo1.jpg
          ├── photo2.jpg
          └── video.mp4
```

The archive must be validated before extraction.

---

# 25. Path Traversal Protection

The receiver must never blindly trust filenames received over the network.

The following must be rejected:

```text
../../secret.txt
..\..\secret.txt
/etc/passwd
C:\Windows\System32\...
```

All extracted files must remain inside the designated HandOff storage directory.

---

# 26. Safe Extraction

Before extracting an archive, the application must validate every archive entry.

Conceptually:

```text
Archive entry
      ↓
Normalize path
      ↓
Resolve destination
      ↓
Check destination is inside allowed directory
      ↓
Extract
```

If any entry escapes the target directory:

```text
Reject entire transfer
```

---

# 27. Filename Sanitization

Filenames must be sanitized before writing to disk.

The application must safely handle:

- Reserved filenames
- Path separators
- Invalid characters
- Extremely long filenames
- Duplicate filenames
- Empty filenames
- Special filesystem names

The original filename should still be preserved in metadata whenever possible.

---

# 28. Filename Collisions

If a received file already exists:

```text
photo.jpg
```

the application should generate:

```text
photo(1).jpg
```

then:

```text
photo(2).jpg
```

and so on.

Existing files must never be silently overwritten.

---

# 29. Controlled Storage

Received files must only be written into HandOff-managed storage.

For example:

```text
HandOff/
└── received/
    └── photo.jpg
```

The remote sender must never be able to specify an arbitrary destination path.

---

# 30. File Integrity

Every managed file has a SHA-256 hash.

Example:

```text
SHA-256:
9f86d081884c7d659a2feaa0c55ad015...
```

The sender computes the hash before transfer.

The receiver computes the hash after receiving the file.

---

# 31. Hash Verification

The receiver must verify:

```text
sender_hash == receiver_hash
```

If they match:

```text
Transfer integrity = VALID
```

If they do not:

```text
Transfer integrity = INVALID
```

The transfer must be marked failed.

Audit event:

```text
INVALID_HASH
```

---

# 32. No Antivirus Scanning

Phase 1 does **not** perform antivirus or malware scanning.

HandOff must not attempt to:

- Execute received files
- Inspect executable behavior
- Run `.exe` files
- Perform sandbox analysis
- Perform antivirus scanning

The operating system's security tools remain responsible for malware detection.

---

# 33. Executable Files

`.exe` files are explicitly supported in Phase 1.

They are treated as ordinary files.

HandOff must:

```text
Receive
↓
Validate
↓
Store
```

It must never:

```text
Receive
↓
Execute
```

---

# 34. No User Authentication

Phase 1 does not contain an account system.

There is no:

```text
Username
Password
Email
OTP
Google Login
Cloud Account
```

Authentication occurs at the device-trust level.

---

# 35. Request Authorization

A request must pass the following checks:

```text
Incoming connection
       ↓
Is it a HandOff device?
       ↓
Is its identity valid?
       ↓
Is the device trusted?
       ↓
Is Receive Mode enabled?
       ↓
Is the request valid?
       ↓
Accept
```

Any failed check results in rejection.

---

# 36. Transfer Request Validation

Before accepting a transfer, validate:

- Device identity
- Transfer ID
- Number of files
- File extensions
- File sizes
- Archive metadata
- Destination rules
- Transfer state
- Receive Mode
- SHA-256 information

Invalid requests must be rejected before writing untrusted data into permanent storage.

---

# 37. Replay Protection

Each transfer receives a unique transfer ID.

A completed transfer ID must not be accepted again as a new transfer.

Example:

```text
tr_001
```

already completed:

```text
tr_001 → REJECT
```

This prevents accidental or malicious replay of the same transfer request.

---

# 38. Transfer Flooding

Phase 1 should include basic request protection.

The receiver should limit:

- Number of simultaneous transfer requests
- Number of active transfer jobs
- Number of files per job
- Maximum file size
- Invalid requests from a device

Since Phase 1 supports only one active transfer:

```text
Active transfers = 1
```

Any additional request should be rejected or deferred.

---

# 39. One Active Transfer

Phase 1 allows:

```text
1 active transfer job
```

at a time.

This is both a product requirement and a security/resource-control mechanism.

It prevents multiple simultaneous transfers from exhausting:

- Memory
- Disk
- CPU
- Network bandwidth

---

# 40. Resource Validation

The receiver should verify available disk space before starting a large transfer.

If insufficient storage is available:

```text
TRANSFER_REJECTED
```

with a clear error message.

The receiver must not begin a transfer that is guaranteed to exceed available storage.

---

# 41. Audit Logging

Security-relevant events must be written to:

```text
audit_logs
```

Examples:

```text
INVALID_DEVICE
DEVICE_CONNECTED
DEVICE_OFFLINE

TLS_FAILURE

UNSUPPORTED_FILE
FILE_TOO_LARGE
INVALID_PATH
INVALID_FILENAME

INVALID_HASH

TRANSFER_REJECTED
TRANSFER_FAILED
```

---

# 42. Audit Log Requirements

Audit logs must contain enough information to diagnose problems.

Example:

```text
Event:
INVALID_HASH

Transfer:
tr_01ABC

Device:
device_002

Timestamp:
2026-10-01T10:30:00Z

Message:
SHA-256 verification failed.
```

---

# 43. Sensitive Data in Logs

The application must never log:

- Private keys
- TLS private credentials
- Passwords
- Authentication tokens
- Complete file contents
- Secret cryptographic material

File names and transfer IDs may be logged.

---

# 44. Security Error Messages

Security failures should produce useful messages.

Bad:

```text
Error 500
```

Good:

```text
Transfer rejected:
The selected file exceeds the 50 MB Phase-1 limit.
```

For developers, the application should additionally record a structured error code in the audit log.

---

# 45. Firewall

HandOff requires local network connectivity.

The installer should request firewall permission when required by the operating system.

The application should expose only the ports required for HandOff communication.

It should not open arbitrary ports.

The exact production port should be centralized as a configuration constant.

---

# 46. Port Configuration

The selected HandOff port must be:

- Documented
- Configurable internally
- Used consistently by discovery and communication
- Validated during startup

The application should detect port conflicts and provide a useful error.

---

# 47. Local API Security

The internal/local API must not expose administrative functionality unnecessarily.

Endpoints should distinguish between:

```text
Local UI operations
```

and:

```text
Remote device operations
```

Remote clients must not be able to invoke arbitrary local application functions.

---

# 48. API Input Validation

Every network API endpoint must validate input.

Never trust:

```text
filename
file size
device ID
transfer ID
file extension
paths
metadata
```

received from another device.

All network input should be treated as untrusted until validated.

---

# 49. OS Permissions

HandOff should request only the filesystem permissions it requires.

The application should not require:

```text
Administrator/root
```

privileges for normal operation unless a specific platform requirement makes it unavoidable.

---

# 50. Secret Storage

Private cryptographic material must be stored in protected application storage.

Requirements:

- Restrictive file permissions
- No world-readable private keys
- No private keys in Git
- No private keys in logs
- No private keys in configuration committed to source control

---

# 51. Development Security

Development credentials must never be committed to Git.

The repository must ignore:

```text
*.key
*.pem
*.crt
.env
secrets/
credentials/
```

unless a file is intentionally a non-secret test fixture.

---

# 52. Production vs Development

Security behavior must not be weakened silently in production.

Development-only behavior such as:

```text
debug logging
test certificates
mock devices
development endpoints
```

must be clearly separated from production behavior.

---

# 53. Security Testing

The Phase-1 test suite must include security tests for:

### Device security

- Unknown device rejected
- Invalid device identity rejected
- Trusted device accepted
- Persistent trust works

### File security

- Unsupported extension rejected
- >50 MB file rejected
- Invalid filename rejected
- Path traversal rejected
- Duplicate filename handled
- Invalid SHA-256 rejected

### Network security

- TLS connection succeeds
- Invalid TLS identity rejected
- Plaintext transfer unavailable
- Invalid requests rejected

### Resource security

- Multiple simultaneous transfers rejected
- Excessive file count rejected
- Insufficient storage handled

---

# 54. Security Boundaries

The main security boundaries are:

```text
┌───────────────────────────────┐
│           UI                  │
└──────────────┬────────────────┘
               │
               ▼
┌───────────────────────────────┐
│      Application Services     │
└──────────────┬────────────────┘
               │
       ┌───────┴────────┐
       ▼                ▼
 Database            Filesystem
       │                │
       └───────┬────────┘
               │
               ▼
        Network Layer
               │
               ▼
        Trusted Devices
```

The network layer is treated as an external boundary.

---

# 55. Security Principles

Claude Code must follow these principles:

1. Never trust network input.
2. Never trust filenames received over the network.
3. Never trust sender-provided file sizes.
4. Never trust sender-provided hashes without verification.
5. Never execute received files.
6. Never overwrite existing files.
7. Never allow paths outside HandOff storage.
8. Never transmit private keys.
9. Never log secrets.
10. Never accept transfers from unknown devices.
11. Always use encrypted transport.
12. Always verify device identity.
13. Always validate file extensions.
14. Always enforce the 50 MB limit.
15. Always verify SHA-256 integrity.
16. Maintain audit logs for security failures.
17. Keep only one active transfer in Phase 1.
18. Do not introduce user accounts or cloud authentication.
19. Do not add antivirus functionality to Phase 1.
20. Do not sacrifice security merely to simplify the implementation.

---

# 56. Phase-1 Security Boundary

The security goal of Phase 1 is:

> **A trusted user should be able to rapidly transfer files between trusted HandOff devices on the same LAN without exposing the system to obvious network, filesystem, or transfer-integrity vulnerabilities.**

Phase 1 intentionally does not attempt to become an enterprise zero-trust file-transfer platform.

Future phases may introduce:

- Advanced device revocation
- Stronger certificate management
- Zero-trust authorization
- Device permissions
- Secure OS key stores
- Remote transfer
- End-to-end cryptographic enhancements
- Malware scanning integration

Those features must not complicate the Phase-1 MVP unnecessarily.

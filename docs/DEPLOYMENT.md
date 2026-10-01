# Deployment

## 1. Purpose

This document defines how HandOff Phase 1 is built, packaged, installed, and executed.

HandOff is a desktop application consisting of:

- Tauri frontend
- Python backend
- SQLite database
- Local filesystem storage
- LAN-based device communication

There is **no cloud or server deployment** in Phase 1.

---

# 2. Deployment Model

HandOff uses a peer-to-peer desktop deployment model.

```text
┌───────────────────┐
│    Laptop A       │
│    HandOff        │
│                   │
│ Tauri + Python    │
│ SQLite + Storage  │
└─────────┬─────────┘
          │
          │ LAN / Wi-Fi
          │
┌─────────▼─────────┐
│    Laptop B       │
│    HandOff        │
│                   │
│ Tauri + Python    │
│ SQLite + Storage  │
└───────────────────┘
```

There is no central HandOff server.

---

# 3. Supported Platforms

Phase 1 supports:

```text
Windows x64
Linux x64
```

ARM is not supported in Phase 1.

---

# 4. Windows Distribution

The first production target is Windows.

The application should be distributed as a Windows installer/application package.

The installer must contain:

```text
Tauri Application
+
Python Runtime
+
Python Backend
+
Required Dependencies
```

The user must not need to manually install Python.

---

# 5. Linux Distribution

Phase 1 must also produce a Linux:

```text
.AppImage
```

The AppImage is primarily required for development and Linux testing during Phase 1.

Example:

```text
HandOff-x86_64.AppImage
```

---

# 6. Linux Package Scope

`.deb` packaging is **not required for Phase 1**.

It may be introduced in a later phase.

Phase 1:

```text
Windows → Installer
Linux   → AppImage
```

---

# 7. Bundled Python Runtime

The Python backend must be bundled with the application.

Users should not need:

```text
Python
pip
virtualenv
Poetry
Conda
```

installed separately.

Expected runtime:

```text
HandOff
│
├── Tauri
│
└── Bundled Python
      │
      └── HandOff Backend
```

---

# 8. Python Dependencies

Python dependencies must be packaged into the application.

The production application must not require Internet access to install Python dependencies at first launch.

Dependencies should be resolved during the build process.

---

# 9. Backend Startup

When HandOff launches:

```text
Application starts
      ↓
Tauri initializes
      ↓
Bundled Python runtime starts
      ↓
Python backend starts
      ↓
SQLite initializes
      ↓
Local API/network service starts
      ↓
Frontend connects
      ↓
HandOff ready
```

The user should see HandOff as a single application.

---

# 10. No External Server

The Python backend is a local process.

It is not deployed to:

```text
AWS
Cloudflare
Vercel
Supabase
Docker server
VPS
```

for Phase 1.

---

# 11. No Cloud Dependency

Core functionality must work without:

```text
Internet
Cloud account
External API
Remote database
```

The only required network connection for transfers is the local LAN/Wi-Fi.

---

# 12. Application Data

Application data must be stored in the operating system's appropriate application-data location.

Conceptually:

```text
HandOff/
├── handoff.db
├── files/
├── received/
├── transfers/
└── temp/
```

The application must not store persistent data inside the installation directory.

---

# 13. Data Persistence During Updates

Updating HandOff must preserve:

```text
Database
Received files
Imported files
Trusted devices
Settings
Transfer history
Audit logs
```

Application upgrades must not overwrite user data.

---

# 14. Uninstallation

Phase 1 has the following behavior:

> Uninstalling HandOff removes HandOff application data.

This includes:

```text
handoff.db
received files
imported files
trusted devices
settings
history
audit logs
```

The uninstaller must clearly communicate this behavior to the user.

The application must not silently leave large amounts of user data behind.

---

# 15. Uninstall Safety

The uninstaller must distinguish between:

```text
Application binaries
```

and:

```text
Application data
```

The removal process should explicitly target the HandOff application-data directory.

It must never delete unrelated user directories.

---

# 16. Desktop Shortcut

A desktop shortcut is optional.

The installer should at minimum provide a Start Menu/application launcher entry where supported.

The user should be able to launch HandOff normally after installation.

---

# 17. Firewall

Phase 1 does not include custom firewall configuration.

HandOff should not require a special firewall management component.

The application should use normal OS networking behavior.

If the operating system itself prompts the user for network access, the user can allow it through the normal OS interface.

---

# 18. Network Requirements

The deployment environment requires:

```text
Same LAN / Wi-Fi
```

Example:

```text
Laptop A ───── Wi-Fi ───── Laptop B
```

Devices must be able to communicate with each other over the local network.

---

# 19. Production Build

Production builds must disable development functionality.

Production builds must not include:

```text
Debug UI
Development APIs
Development certificates
Development keys
Verbose development-only logging
Test endpoints
Mock devices
```

---

# 20. Production Configuration

Production configuration must be separate from development configuration.

Example:

```text
Development
    ↓
Debug enabled
Test environment
Mock data

Production
    ↓
Debug disabled
Real network
Real database
Real storage
```

---

# 21. Cryptographic Material

Production device identity must be generated on the user's machine.

Build artifacts must never contain a shared private device key.

Each installed HandOff instance must generate its own identity.

Incorrect:

```text
Build
 └── shared-private-key
```

Correct:

```text
Installation A → generates private key A
Installation B → generates private key B
```

---

# 22. Release Signing

Phase 1 uses:

```text
Unsigned builds
```

Code signing is not required for the MVP.

This may result in operating-system security warnings during installation.

Code signing should be considered in a future release.

---

# 23. Release Channel

Phase 1 has only one release channel:

```text
Stable
```

There are no:

```text
Beta
Nightly
Canary
Experimental
```

release channels.

---

# 24. Automatic Updates

Phase 1 does not implement automatic updates.

Users manually install newer releases.

Example:

```text
v0.1.0
   ↓
Download v0.2.0
   ↓
Install
   ↓
Existing data preserved
```

An automatic update system may be introduced later.

---

# 25. Versioning

HandOff should use semantic versioning:

```text
MAJOR.MINOR.PATCH
```

Example:

```text
0.1.0
0.1.1
0.2.0
1.0.0
```

Phase-1 development can remain under:

```text
0.x.x
```

until the architecture is stable.

---

# 26. Build Artifacts

A release should eventually produce:

```text
Windows:
HandOff-x64-setup.exe

Linux:
HandOff-x86_64.AppImage
```

The exact filenames may change according to Tauri's bundling configuration.

---

# 27. Build Environment

The build process must be reproducible.

The repository should define:

```text
Node/Tauri dependencies
Python dependencies
Rust dependencies
Build configuration
Environment requirements
```

in the project documentation/configuration.

A developer should be able to clone the repository and reproduce the build.

---

# 28. Development vs Release Builds

### Development

```text
Tauri Dev
+
Python Development Backend
+
Development Database
+
Debug Logging
```

### Release

```text
Tauri Production Build
+
Bundled Python Runtime
+
Production Backend
+
Production Configuration
```

Development dependencies must not accidentally become production runtime requirements.

---

# 29. Build Validation

Before producing a release artifact:

```text
Run tests
     ↓
Run security tests
     ↓
Build backend
     ↓
Build Tauri application
     ↓
Package application
     ↓
Install on clean system
     ↓
Run application
     ↓
Test LAN transfer
```

A build that compiles successfully is not automatically considered a valid release.

---

# 30. Clean Installation Testing

Each release should be tested on a clean environment.

Windows:

```text
Clean Windows x64
      ↓
Install HandOff
      ↓
Launch
      ↓
Verify backend starts
      ↓
Verify database initializes
      ↓
Verify device discovery
```

Linux:

```text
Clean Linux x64
      ↓
Run AppImage
      ↓
Launch
      ↓
Verify backend starts
      ↓
Verify database initializes
      ↓
Verify device discovery
```

---

# 31. Upgrade Testing

Before release, test:

```text
HandOff v0.1.0
      ↓
Create files
      ↓
Connect devices
      ↓
Generate history
      ↓
Install v0.2.0
      ↓
Verify data remains
```

The upgrade must not accidentally reset:

```text
devices
files
settings
history
audit_logs
```

---

# 32. Database Compatibility

When application versions change database schemas, the application must safely handle the existing database.

Since Phase 1 does not use Alembic, schema upgrade logic must be implemented by the application where necessary.

The application must never silently destroy the existing database simply because its schema changed.

---

# 33. Build-Time Secrets

No secrets may be embedded into release builds.

Never package:

```text
Private keys
Developer credentials
API keys
Cloud credentials
Test secrets
```

into the application.

---

# 34. Release Checklist

Before publishing a stable release:

```text
[ ] Windows x64 build succeeds
[ ] Linux x64 AppImage builds
[ ] Backend bundled correctly
[ ] Python runtime bundled correctly
[ ] No development APIs included
[ ] No development keys included
[ ] No secrets included
[ ] Debug mode disabled
[ ] Unit tests pass
[ ] Integration tests pass
[ ] Security tests pass
[ ] Clean Windows installation tested
[ ] Clean Linux installation tested
[ ] LAN transfer tested
[ ] Multi-file transfer tested
[ ] Upgrade tested
[ ] Uninstall tested
[ ] Application data behavior verified
```

---

# 35. Future CI/CD

Automated release builds are planned for a later phase.

Future GitHub Actions workflow:

```text
Git Tag
  │
  ▼
GitHub Actions
  │
  ├── Windows x64 build
  │
  └── Linux x64 build
          │
          ▼
Release artifacts
```

Example:

```text
v0.2.0
```

could automatically generate:

```text
HandOff Windows installer
HandOff Linux AppImage
```

This is **not a Phase-1 implementation requirement**.

---

# 36. Deployment Rules for Claude Code

Claude Code must follow these rules:

1. HandOff is a desktop application, not a web application.
2. Do not introduce a cloud backend.
3. Do not introduce a central server.
4. Bundle the Python runtime.
5. Bundle Python dependencies.
6. Support Windows x64.
7. Support Linux x64 through AppImage.
8. Do not require users to install Python.
9. Keep persistent application data outside the installation directory.
10. Preserve user data during application upgrades.
11. Remove HandOff application data during uninstall.
12. Do not implement automatic updates in Phase 1.
13. Do not implement `.deb` packaging in Phase 1.
14. Do not require custom firewall configuration.
15. Do not include development secrets in release builds.
16. Generate device cryptographic identity per installation.
17. Do not ship a shared private key.
18. Disable development/debug functionality in production.
19. Use unsigned builds for the MVP.
20. Keep the release channel stable-only.

---

# 37. Phase-1 Deployment Definition

HandOff Phase 1 deployment is successful when:

```text
Windows x64
     │
     ├── Install HandOff
     ├── Launch without Python installed
     ├── Discover another HandOff device
     └── Transfer files over LAN


Linux x64
     │
     ├── Run AppImage
     ├── Launch without Python installed
     ├── Discover another HandOff device
     └── Transfer files over LAN
```

No cloud infrastructure or external server is required.

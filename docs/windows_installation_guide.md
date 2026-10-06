# HandOff: Windows Installation Guide

> **Status:** the Windows build has been configured but **not yet built or run**. Everything here follows the
> project's build setup (`docs/DEPLOYMENT.md` §37). Expect to fix small issues on the first run and report them.
> Windows must build its own installer: the Python core is frozen with PyInstaller, which cannot cross-compile.

Target: Windows 10/11 **x64**. ARM is not supported in Phase 1.

---

## 1. Prerequisites (one-time)

Install these in this order.

| # | Tool | Where / how | Check |
|---|---|---|---|
| 1 | **Git** | https://git-scm.com/download/win (this also installs **Git Bash**, which you need) | `git --version` |
| 2 | **Visual Studio Build Tools** | https://visualstudio.microsoft.com/visual-cpp-build-tools/ and tick **"Desktop development with C++"** | open "x64 Native Tools" or just continue |
| 3 | **Rust (MSVC toolchain)** | https://rustup.rs and accept the default `x86_64-pc-windows-msvc` | `rustc --version` |
| 4 | **Node.js 20** | https://nodejs.org (LTS 20.x) | `node -v` |
| 5 | **pnpm 9** | `npm install -g pnpm@9` | `pnpm -v` |
| 6 | **uv** | PowerShell: `irm https://astral.sh/uv/install.ps1 \| iex` | `uv --version` |
| 7 | **WebView2 runtime** | Preinstalled on Windows 11 and updated Windows 10. Otherwise https://developer.microsoft.com/microsoft-edge/webview2/ | - |

You do **not** need to install Python yourself: `uv` downloads Python 3.13 on demand, and the finished installer
bundles its own runtime.

After installing, **close and reopen** your terminals so the new PATH takes effect.

---

## 2. Get the code

PowerShell or Git Bash:

```bash
git clone https://github.com/joeljohngeorge8080/HandOff.git
cd HandOff
git checkout m3-m4-ui-and-packaging      # until it is merged to main
```

---

## 3. Option A: quick test in dev mode (no installer)

Good for checking that everything works before building the installer. Debug builds start the core with `uv run`.

```powershell
cd app
pnpm install
pnpm tauri dev
```

The first build compiles Rust and takes several minutes. The HandOff window opens when it is done.

---

## 4. Option B: build the real installer

Use **Git Bash** (the sidecar script is a bash script). From the repo root:

```bash
# 1) Freeze the Python core into a bundle (needs uv)
./scripts/build-sidecar.sh

# 2) Build the installer
cd app
pnpm install
pnpm tauri build --bundles nsis
```

Result (the exact name may vary with the version):

```
app/src-tauri/target/release/bundle/nsis/HandOff_0.1.0_x64-setup.exe
```

Notes:
- The build needs Internet **once** (to download Python packages, Rust crates and the NSIS tool). The installed app
  needs **no** Internet.
- The installer is a **per-user** install, so no administrator rights are required.

---

## 5. Install and first run

1. Double-click the `...-setup.exe`.
2. **SmartScreen warning ("Windows protected your PC"):** builds are unsigned by design in Phase 1. Click
   **More info → Run anyway**.
3. Finish the installer and launch **HandOff** from the Start Menu.
4. **Windows Firewall prompt:** tick **Private networks** and click **Allow access**. HandOff does not change the
   firewall itself, so this is the normal OS prompt.
5. The window should show **"This device: <your PC name>"** and **Receive: OFF**. That means the core started.

Make sure your network is set to **Private**: Settings → Network & Internet → Wi-Fi (or Ethernet) → your network →
**Private network**. On a Public profile Windows blocks incoming connections and discovery.

---

## 6. Connect to another HandOff device (Linux or Windows)

Both devices must be on the **same Wi-Fi/LAN** (not guest Wi-Fi, and no router "client isolation").

1. On the **receiving** device, switch **Receive: ON** (top right).
2. On the **sending** device, click **+** under **Devices**. The other device should appear. Click **Connect**.
3. Click **Add files** and pick files (`.txt .jpg .mp4 .exe`, each 50 MB or less). Select them with click, Ctrl+click,
   Shift+click or a drag rectangle.
4. Click **Send to <device name>**. Progress shows on the right. A notification appears when the transfer finishes.

Received files and history are kept under `%APPDATA%\HandOff`.

---

## 7. Where things live

| What | Where |
|---|---|
| Application | `%LOCALAPPDATA%\HandOff` (per-user install; exact path chosen by the installer) |
| Your data (database, imported/received files, keys, history) | `%APPDATA%\HandOff` |
| Device identity (private key) | `%APPDATA%\HandOff\keys`: never shared, never leaves the machine |

Updating HandOff (running a newer installer) keeps `%APPDATA%\HandOff`. There is no auto-update; install new versions
manually.

---

## 8. Uninstall

Settings → Apps → **HandOff** → Uninstall. The uninstaller asks whether to also delete HandOff's data
(transferred files, imported files, trusted devices, history, settings). It removes **only** `%APPDATA%\HandOff` and
no other folder. Choose **No** to keep your data for a reinstall.

---

## 9. Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `pnpm tauri build` fails with a linker or `link.exe` error | Visual Studio Build Tools "Desktop development with C++" not installed |
| `build-sidecar.sh: uv: command not found` | Reopen Git Bash after installing uv, or add `%USERPROFILE%\.local\bin` to PATH |
| App opens but shows an error that the core is missing or stopped | The sidecar did not get bundled. Re-run `./scripts/build-sidecar.sh` **before** `pnpm tauri build` |
| Antivirus quarantines `handoff-core.exe` | Known false positive for PyInstaller-built programs. Add an exception for the HandOff install folder |
| No other device appears in the list | Both on the same subnet (`ipconfig` / `ip a`)? Network profile **Private**? Firewall allowed on Private? Guest Wi-Fi or client isolation off? Try a phone hotspot to rule out the router |
| Device appears but Connect fails | Firewall is blocking TCP port **8765** (HandOff's peer port): allow HandOff on Private networks |
| Send says receive mode is disabled | Turn **Receive: ON** on the receiving device |
| A file is rejected | Only `.txt .jpg .mp4 .exe` (any letter case) and up to 50 MB (52,428,800 bytes) are allowed |

When reporting a problem, include the exact error text. To see the core's log messages, start the installed app from
a terminal.

---

## 10. Quick checklist for the Windows ↔ Linux test

- [ ] Windows installer builds and installs
- [ ] App launches, shows device name, Receive: OFF
- [ ] Linux device appears in the Windows device list (and the reverse)
- [ ] Connect works in both directions
- [ ] Single file and multi-file transfer succeed, with correct contents
- [ ] `.exe` arrives but is never executed
- [ ] 50 MB file is accepted; larger is rejected with a clear reason
- [ ] Same filename sent twice arrives as `name(1).ext`
- [ ] Notifications appear on completion
- [ ] History and Receive Mode survive a restart
- [ ] Closing one app marks the peer offline on the other
- [ ] Uninstall removes only `%APPDATA%\HandOff`

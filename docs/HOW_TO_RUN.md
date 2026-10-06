# How to Run HandOff

## 1. The packaged AppImage (no Python needed)

```bash
cd ~/labs/git-lab/handoff
./app/src-tauri/target/release/bundle/appimage/HandOff_0.1.0_amd64.AppImage
```

Data is stored in `~/.local/share/HandOff`.

To rebuild it first:

```bash
./scripts/build-sidecar.sh
cd app && pnpm tauri build --bundles appimage
```

## 2. Dev mode (runs the core from source)

```bash
cd ~/labs/git-lab/handoff/app
pnpm install            # first time only
pnpm tauri dev
```

## 3. Two instances on one laptop

Dev builds only. The `HANDOFF_*` overrides are compiled out of release builds.
Each instance needs its own data dir and port, so each gets its own identity and database.

Terminal 1, instance A:

```bash
cd ~/labs/git-lab/handoff/app
HANDOFF_DATA_DIR=/tmp/ho-a HANDOFF_PORT=8765 pnpm tauri dev
```

Terminal 2, instance B (start it after A's window is up; it reuses A's Vite server on port 1420):

```bash
cd ~/labs/git-lab/handoff/app/src-tauri
HANDOFF_DATA_DIR=/tmp/ho-b HANDOFF_PORT=8766 ./target/debug/handoff
```

Do not start B with `pnpm tauri dev`: port 1420 is already taken.
The two windows open on top of each other. Drag one aside, or use
`wmctrl -l` and `wmctrl -i -r <id> -e 0,960,60,960,620`.

Optional override: `HANDOFF_BIND=<address>` sets the address the peer API binds to.

### Test flow

1. In **B**, switch **Receive: ON** (top right).
2. In **A**, click **+** under Devices, then **Connect** next to B.
3. In **A**, click **Add files** (`.txt .jpg .mp4 .exe`, 50 MB or less), select some tiles, click **Send to ...**.
4. Check progress, history and the notification on both sides.
5. Close both windows and reopen them with the same commands. History and Receive Mode should persist.

### Caveats

- Same-machine mDNS discovery is **not yet confirmed**. Both instances advertise the machine's LAN address on
  different ports, which usually works. If the device list stays empty, check that the Wi-Fi/ethernet interface is up.
  Loopback-only setups and some VPNs block multicast. Two real machines on the same Wi-Fi is the reliable fallback.
- Reset: `rm -rf /tmp/ho-a /tmp/ho-b` gives both instances fresh identities. Reconnecting after a reset is a new
  trust relationship, because trust is stored per device.

## 4. Linux firewall (ufw)

HandOff never changes the firewall (ADR-027). If `ufw` is active, it drops incoming TCP on the peer port
(**8765**) while mDNS (UDP 5353) still works. Symptom: the other device appears in the list, but **Connect**
or **Send** from it fails with "The device could not be reached", while connecting *from* this machine works.
Linux-to-Linux tests on one machine don't show it, because loopback traffic is always allowed.

Allow the port for your LAN only (replace the subnet with your own, see `ip -4 addr`):

```bash
sudo ufw allow from 10.180.183.0/24 to any port 8765 proto tcp comment 'HandOff'
sudo ufw status
```

Check from the other machine (Windows PowerShell): `Test-NetConnection <this-ip> -Port 8765` should report
`TcpTestSucceeded : True`. If you changed `HANDOFF_PORT` in a dev build, open that port instead.

## 5. Tests (no GUI)

```bash
cd backend && uv run pytest        # backend suite
cd app && pnpm vitest run          # UI logic tests
```

See `docs/DEPLOYMENT.md` §37 for the full build steps.

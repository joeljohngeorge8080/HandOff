#!/usr/bin/env bash
# Builds the Python core as a onedir bundle at app/src-tauri/binaries/handoff-core/.
# Run on the target OS (PyInstaller does not cross-compile). Needs: uv. Internet only for the one-time model fetch.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
out="$root/app/src-tauri/binaries"
"$root/scripts/fetch-hand-model.sh"  # pinned + verified; the app never downloads it (ADR-056)
case "$(uname -s)" in MINGW*|MSYS*|CYGWIN*) sep=";" ;; *) sep=":" ;; esac
cd "$root/backend"
rm -rf build dist "$out/handoff-core"
uv run --group build pyinstaller --noconfirm --clean \
  --name handoff-core --onedir --console \
  --paths src \
  --collect-submodules uvicorn --collect-submodules zeroconf \
  --collect-submodules sqlalchemy.dialects.sqlite \
  --hidden-import cryptography \
  --collect-all mediapipe --collect-submodules pyautogui \
  --add-data "models/hand_landmarker.task${sep}." \
  --exclude-module pytest --exclude-module mypy --exclude-module tkinter \
  packaging/entry.py
mkdir -p "$out"
mv dist/handoff-core "$out/handoff-core"
echo "sidecar -> $out/handoff-core"

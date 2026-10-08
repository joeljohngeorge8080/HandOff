#!/usr/bin/env bash
# Fetches the MediaPipe hand-landmarker model into backend/models/ and verifies a pinned SHA-256.
# Build-time only (ADR-056): the app never downloads it. Re-run is a no-op when the file matches.
set -euo pipefail
url="https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task"
sha256="fbc2a30080c3c557093b5ddfc334698132eb341044ccee322ccf8bcf3607cde1"
root="$(cd "$(dirname "$0")/.." && pwd)"
dest="$root/backend/models/hand_landmarker.task"
mkdir -p "$(dirname "$dest")"
hash_of() { if command -v sha256sum >/dev/null; then sha256sum "$1" | cut -d' ' -f1; else shasum -a 256 "$1" | cut -d' ' -f1; fi; }
if [ -f "$dest" ] && [ "$(hash_of "$dest")" = "$sha256" ]; then
  echo "model ok -> $dest"; exit 0
fi
tmp="$(mktemp)"; trap 'rm -f "$tmp"' EXIT
curl -fsSL -o "$tmp" "$url"
if [ "$(hash_of "$tmp")" != "$sha256" ]; then
  echo "hand model checksum mismatch; refusing to use it" >&2; exit 1
fi
mv "$tmp" "$dest"; trap - EXIT
echo "model fetched -> $dest"

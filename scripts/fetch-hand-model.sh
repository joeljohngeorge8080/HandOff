#!/usr/bin/env bash
# Thin wrapper: the logic (and the pinned SHA-256) lives in fetch_hand_model.py, which also runs
# on Windows without bash.
set -euo pipefail
exec python3 "$(cd "$(dirname "$0")" && pwd)/fetch_hand_model.py"

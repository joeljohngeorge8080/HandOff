"""Fetch the MediaPipe hand-landmarker model into backend/models/ (cross-platform, stdlib only).

Build/dev time only (ADR-056): the app never downloads it. The SHA-256 is pinned; a file that
does not match is refused. Re-running is a no-op when the file is already correct.
    python scripts/fetch_hand_model.py
"""

import hashlib
import sys
import tempfile
import urllib.request
from pathlib import Path

URL = (
    "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/"
    "float16/1/hand_landmarker.task"
)
SHA256 = "fbc2a30080c3c557093b5ddfc334698132eb341044ccee322ccf8bcf3607cde1"
DEST = Path(__file__).resolve().parents[1] / "backend" / "models" / "hand_landmarker.task"


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    if DEST.is_file() and sha256_of(DEST) == SHA256:
        print(f"model ok -> {DEST}")
        return 0
    DEST.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=DEST.parent, delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        urllib.request.urlretrieve(URL, tmp_path)  # noqa: S310 - fixed https URL
        if sha256_of(tmp_path) != SHA256:
            print("hand model checksum mismatch; refusing to use it", file=sys.stderr)
            return 1
        tmp_path.replace(DEST)
    finally:
        tmp_path.unlink(missing_ok=True)
    print(f"model fetched -> {DEST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

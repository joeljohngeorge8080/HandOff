"""PyInstaller entry point for the HandOff sidecar (production)."""

from handoff.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())

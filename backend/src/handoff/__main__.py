"""Sidecar entry point: `python -m handoff --data-dir <dir>`.

stdout is reserved for the IPC protocol (JSON lines). All logging goes to stderr.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from handoff import __version__
from handoff.config import HISTORY_CLEANUP_INTERVAL_SECONDS, MAX_IPC_LINE_BYTES, PEER_PORT
from handoff.core import Core
from handoff.devices.discovery import StaticDiscovery
from handoff.errors import HandOffError
from handoff.history import PeriodicTask
from handoff.ipc.dispatcher import Dispatcher
from handoff.ipc.protocol import encode_error
from handoff.network import Network, NetworkConfig
from handoff.paths import AppPaths, default_data_dir

log = logging.getLogger("handoff")

_WORKERS = 4  # a slow request (e.g. connecting to an unreachable device) must not block the UI


def _read_line(stream: object) -> str | None:
    """Read one request line with a size cap; oversized lines are drained and rejected."""
    line = stream.readline(MAX_IPC_LINE_BYTES + 1)  # type: ignore[attr-defined]
    if not line:
        return None
    if len(line) > MAX_IPC_LINE_BYTES and not line.endswith("\n"):
        while True:
            rest = stream.readline(MAX_IPC_LINE_BYTES)  # type: ignore[attr-defined]
            if not rest or rest.endswith("\n"):
                break
        return "x" * (MAX_IPC_LINE_BYTES + 1)  # forces the dispatcher's size rejection
    return str(line)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="handoff")
    parser.add_argument("--data-dir", type=Path, default=None, help="application data directory")
    parser.add_argument("--port", type=int, default=PEER_PORT, help="peer API port (0 = any free)")
    parser.add_argument("--bind", default=None, help="address to bind (default: the LAN address)")
    parser.add_argument("--no-network", action="store_true", help="local only (testing)")
    parser.add_argument("--no-discovery", action="store_true", help="disable mDNS (testing)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    core = Core(AppPaths(args.data_dir or default_data_dir()))
    network: Network | None = None
    try:
        core.start()
        if not args.no_network:
            network = Network(
                core,
                NetworkConfig(
                    host=args.bind,
                    port=args.port,
                    discovery=StaticDiscovery() if args.no_discovery else None,
                ),
            )
            network.start()
    except HandOffError as exc:
        # Show the user a clear message, then stop; never fall back to another database/port.
        log.error("Startup failed: %s", exc.message)
        sys.stdout.write(encode_error(None, exc) + "\n")
        sys.stdout.flush()
        if network:
            network.stop()
        if hasattr(core, "db"):
            core.close()
        return 1

    cleanup = PeriodicTask(
        core.history.cleanup, HISTORY_CLEANUP_INTERVAL_SECONDS, "history-cleanup"
    )
    cleanup.start()
    dispatcher = Dispatcher(core, network)
    out_lock = threading.Lock()

    def emit(text: str) -> None:
        with out_lock:
            sys.stdout.write(text + "\n")
            sys.stdout.flush()

    def work(line: str) -> None:
        emit(dispatcher.handle_line(line))

    ready: dict[str, object] = {
        "event": "ready",
        "version": __version__,
        "device_id": core.identity.device_id,
    }
    if network:
        ready["port"] = network.port
    emit(json.dumps(ready))

    pool = ThreadPoolExecutor(max_workers=_WORKERS, thread_name_prefix="ipc")
    try:
        while (line := _read_line(sys.stdin)) is not None:
            if line.strip():
                pool.submit(work, line)
    finally:
        pool.shutdown(wait=True)
        cleanup.stop()
        if network:
            network.stop()
        core.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Runs the peer API with uvicorn on a background thread (HTTPS only)."""

from __future__ import annotations

import logging
import os
import socket
import threading
import time

import uvicorn
from fastapi import FastAPI

from handoff.errors import HandOffError

log = logging.getLogger(__name__)

_START_TIMEOUT = 15.0


def check_port_free(host: str, port: int) -> None:
    """SECURITY §46: report a port conflict clearly instead of failing obscurely later."""
    if port == 0:
        return
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if os.name == "posix":
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
        except OSError as exc:
            raise HandOffError(
                "NETWORK_ERROR",
                f"Port {port} is already in use. Close the other program or application.",
            ) from exc


class PeerServer:
    def __init__(self, app: FastAPI, host: str, port: int, cert_file: str, key_file: str) -> None:
        self.host, self.requested_port = host, port
        self._server = uvicorn.Server(
            uvicorn.Config(
                app,
                host=host,
                port=port,
                ssl_certfile=cert_file,
                ssl_keyfile=key_file,
                log_config=None,
                log_level="warning",
                access_log=False,
                lifespan="off",
                ws="none",
                server_header=False,
                date_header=False,
                limit_concurrency=64,
                timeout_keep_alive=5,
                timeout_graceful_shutdown=2,
            )  # fmt: skip
        )
        self._thread = threading.Thread(target=self._server.run, name="peer-api", daemon=True)

    def start(self) -> int:
        check_port_free(self.host, self.requested_port)
        self._thread.start()
        deadline = time.monotonic() + _START_TIMEOUT
        while not self._server.started:
            if not self._thread.is_alive() or time.monotonic() > deadline:
                raise HandOffError("NETWORK_ERROR", "The peer API server could not start.")
            time.sleep(0.02)
        return int(self._server.servers[0].sockets[0].getsockname()[1])

    def stop(self) -> None:
        self._server.should_exit = True
        if self._thread.is_alive():  # never started (e.g. the port was taken): nothing to join
            self._thread.join(timeout=10)

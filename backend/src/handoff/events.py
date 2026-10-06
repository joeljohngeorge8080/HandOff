"""Core-to-UI push events (ADR-054).

The UI used to poll for transfer progress. The core now pushes `transfer.updated` and
`connection.changed` as JSON lines on stdout, so the edge animations follow real state without
a fast poll. Events are only a *notification*: the database stays the source of truth, a
payload is always built from it, and a UI that misses an event can still call
`status.snapshot`.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Any, Protocol

log = logging.getLogger(__name__)

PROGRESS_INTERVAL_SECONDS = 0.25

Sink = Callable[[str, dict[str, Any]], None]


class _History(Protocol):
    def get(self, transfer_id: str) -> dict[str, Any] | None: ...


class EventBus:
    def __init__(self) -> None:
        self._sinks: list[Sink] = []
        self._lock = threading.Lock()
        self._last_progress: dict[str, float] = {}
        self.history: _History | None = None  # set by Core once the services exist

    def subscribe(self, sink: Sink) -> None:
        with self._lock:
            self._sinks.append(sink)

    @property
    def active(self) -> bool:
        return bool(self._sinks)

    def publish(self, name: str, data: dict[str, Any]) -> None:
        with self._lock:
            sinks = list(self._sinks)
        for sink in sinks:
            try:
                sink(name, data)
            except Exception:  # a broken listener must never break a transfer
                log.exception("Event sink failed for %s", name)

    def progress_due(self, transfer_id: str) -> bool:
        """Cheap check (no I/O) so hot loops can skip building a payload."""
        if not self._sinks:
            return False
        last = self._last_progress.get(transfer_id)
        return last is None or time.monotonic() - last >= PROGRESS_INTERVAL_SECONDS

    def transfer_changed(
        self, transfer_id: str, *, bytes_transferred: int | None = None, throttle: bool = False
    ) -> None:
        """Publish the transfer's current state. `throttle` limits progress events to ~4 Hz."""
        if not self._sinks or self.history is None:
            return
        if throttle:
            if not self.progress_due(transfer_id):
                return
            self._last_progress[transfer_id] = time.monotonic()
        try:
            payload = self.history.get(transfer_id)
        except Exception:
            log.exception("Could not build the event for %s", transfer_id)
            return
        if payload is None:
            return
        if bytes_transferred is not None:
            payload["bytes_transferred"] = max(payload["bytes_transferred"], bytes_transferred)
        if payload["status"] in ("completed", "failed", "partially_completed"):
            self._last_progress.pop(transfer_id, None)
        self.publish("transfer.updated", payload)

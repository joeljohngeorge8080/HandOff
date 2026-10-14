"""Maps IPC actions to core services (API §29-31). The UI talks only to this layer."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from handoff.core import Core
from handoff.errors import HandOffError
from handoff.ipc.protocol import Request, encode_error, encode_result, parse_request
from handoff.network import Network

log = logging.getLogger(__name__)

Handler = Callable[[dict[str, Any]], dict[str, Any]]

MAX_HISTORY_PAGE = 200


def _str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise HandOffError("INVALID_REQUEST", f"'{key}' must be a non-empty string.")
    return value


def _int(payload: dict[str, Any], key: str, default: int, lo: int, hi: int) -> int:
    value = payload.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        raise HandOffError("INVALID_REQUEST", f"'{key}' must be an integer from {lo} to {hi}.")
    return value


class Dispatcher:
    def __init__(self, core: Core, network: Network | None = None) -> None:
        self.core = core
        self.network = network
        self._handlers: dict[str, Handler] = {
            "files.list": self._files_list,
            "files.add": self._files_add,
            "files.get": self._files_get,
            "files.delete": self._files_delete,
            "settings.get": self._settings_get,
            "settings.set": self._settings_set,
            "history.list": self._history_list,
            "status.snapshot": self._status_snapshot,
            "cv.status": self._cv_status,
            "devices.discover": self._devices_discover,
            "devices.list": self._devices_list,
            "devices.connect": self._devices_connect,
            "devices.status": self._devices_status,
            "drop.inspect": self._drop_inspect,
            "drop.send": self._drop_send,
            "transfer.create": self._transfer_create,
            "transfer.status": self._transfer_status,
        }

    def handle_line(self, line: str) -> str:
        """Always returns one response line; never raises."""
        req: Request | None = None
        try:
            req = parse_request(line)
            handler = self._handlers.get(req.action)
            if handler is None:
                raise HandOffError("INVALID_REQUEST", f"Unknown action '{req.action}'.")
            return encode_result(req.id, handler(req.payload))
        except HandOffError as exc:
            return encode_error(req.id if req else None, exc)
        except Exception:
            log.exception("Unhandled error in action %s", req.action if req else "?")
            return encode_error(
                req.id if req else None,
                HandOffError("INTERNAL_ERROR", "An unexpected error occurred."),
            )

    # ----- files --------------------------------------------------------------------------

    def _files_list(self, _p: dict[str, Any]) -> dict[str, Any]:
        return {"files": self.core.files.list_files()}

    def _files_add(self, p: dict[str, Any]) -> dict[str, Any]:
        """Add several files; each succeeds or is rejected on its own (UI multi-select)."""
        paths = p.get("paths")
        if not isinstance(paths, list) or not paths or not all(isinstance(x, str) for x in paths):
            raise HandOffError("INVALID_REQUEST", "'paths' must be a non-empty list of strings.")
        added: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        for path in paths:
            try:
                added.append(self.core.files.import_file(path))
            except HandOffError as exc:
                rejected.append({"path": path, **exc.to_dict()})
        return {"added": added, "rejected": rejected}

    def _files_get(self, p: dict[str, Any]) -> dict[str, Any]:
        return self.core.files.get_file(_str(p, "file_id"))

    def _files_delete(self, p: dict[str, Any]) -> dict[str, Any]:
        file_id = _str(p, "file_id")
        self.core.files.delete_file(file_id)
        return {"deleted": file_id}

    # ----- settings ------------------------------------------------------

    def _settings_get(self, _p: dict[str, Any]) -> dict[str, Any]:
        return {"settings": self.core.settings.get_all()}

    def _settings_set(self, p: dict[str, Any]) -> dict[str, Any]:
        key = _str(p, "key")
        if "value" not in p:
            raise HandOffError("INVALID_REQUEST", "'value' is required.")
        self.core.settings.set(key, p["value"])
        if key == "hand_control_enabled":
            if self.core.settings.hand_control_enabled():
                self.core.hand_control.start()
            else:
                self.core.hand_control.stop()
        elif key == "hand_scroll_enabled" and self.core.settings.hand_control_enabled():
            self.core.hand_control.restart()  # the worker reads the switch when it starts
        return {"settings": self.core.settings.get_all()}

    def _cv_status(self, _p: dict[str, Any]) -> dict[str, Any]:
        return {"hand_control": self.core.hand_control.status()}

    # ----- history and status -------------------------------------------------------------

    def _history_list(self, p: dict[str, Any]) -> dict[str, Any]:
        limit = _int(p, "limit", 50, 1, MAX_HISTORY_PAGE)
        offset = _int(p, "offset", 0, 0, 10**9)
        return {"items": self.core.history.list(limit, offset)}

    def _status_snapshot(self, _p: dict[str, Any]) -> dict[str, Any]:
        return {
            "device": {
                "device_id": self.core.identity.device_id,
                "device_name": self.core.device_name,
            },
            "receive_directory": str(self.core.settings.receive_directory(validate=False)),
            "connection": (
                self.network.connections.snapshot()
                if self.network
                else {"connected": False, "device": None}
            ),
            "hand_control": self.core.hand_control.status(),
            "active_transfer": self.core.history.active(),
            "recent_history": self.core.history.list(10, 0),
        }

    # ----- devices and transfers (need the network) ---------------------------------------

    def _net(self) -> Network:
        if self.network is None:
            raise HandOffError("INVALID_STATE", "Networking is not running.")
        return self.network

    def _devices_discover(self, _p: dict[str, Any]) -> dict[str, Any]:
        return {"devices": self._net().connections.discover()}

    def _devices_list(self, _p: dict[str, Any]) -> dict[str, Any]:
        return {"devices": self._net().connections.list_known()}

    def _devices_connect(self, p: dict[str, Any]) -> dict[str, Any]:
        return {"connection": self._net().connections.connect(_str(p, "device_id"))}

    def _devices_status(self, _p: dict[str, Any]) -> dict[str, Any]:
        return {"connection": self._net().connections.snapshot()}

    def _drop_inspect(self, p: dict[str, Any]) -> dict[str, Any]:
        return self._net().drops.inspect(p.get("paths"))

    def _drop_send(self, p: dict[str, Any]) -> dict[str, Any]:
        return self._net().drops.send(p.get("paths"))

    def _transfer_create(self, p: dict[str, Any]) -> dict[str, Any]:
        net = self._net()
        return {"transfer": net.sender.create(p.get("file_ids"), p.get("destination_device_id"))}

    def _transfer_status(self, p: dict[str, Any]) -> dict[str, Any]:
        self._net()
        transfer = self.core.history.get(_str(p, "transfer_id"))
        if transfer is None:
            raise HandOffError("TRANSFER_NOT_FOUND", "Transfer not found.")
        return {"transfer": transfer}

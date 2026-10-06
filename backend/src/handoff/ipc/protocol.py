"""Local IPC wire format: one JSON object per line over the sidecar's stdin/stdout.

Request:  {"id": <str|int>, "action": "files.list", "payload": {...}}
Response: {"id": <same>, "result": {...}}  or  {"id": <same>, "error": {code, message, details?}}
Event:    {"event": "transfer.updated", "data": {...}}  (pushed by the core, no id; ADR-054)
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from handoff.config import MAX_IPC_LINE_BYTES
from handoff.errors import HandOffError


@dataclass(frozen=True)
class Request:
    id: str | int
    action: str
    payload: dict[str, Any]


def parse_request(line: str) -> Request:
    if len(line.encode("utf-8", "replace")) > MAX_IPC_LINE_BYTES:
        raise HandOffError("INVALID_REQUEST", "Request is too large.")
    try:
        data = json.loads(line)
    except ValueError as exc:
        raise HandOffError("INVALID_REQUEST", "Request is not valid JSON.") from exc
    if not isinstance(data, dict):
        raise HandOffError("INVALID_REQUEST", "Request must be a JSON object.")
    req_id, action, payload = data.get("id"), data.get("action"), data.get("payload", {})
    if isinstance(req_id, bool) or not isinstance(req_id, str | int):
        raise HandOffError("INVALID_REQUEST", "Request 'id' must be a string or integer.")
    if not isinstance(action, str) or not action:
        raise HandOffError("INVALID_REQUEST", "Request 'action' must be a non-empty string.")
    if not isinstance(payload, dict):
        raise HandOffError("INVALID_REQUEST", "Request 'payload' must be an object.")
    return Request(req_id, action, payload)


def encode_result(req_id: str | int | None, result: dict[str, Any]) -> str:
    return json.dumps({"id": req_id, "result": result})


def encode_error(req_id: str | int | None, error: HandOffError) -> str:
    return json.dumps({"id": req_id, **error.to_dict()})


def encode_event(name: str, data: dict[str, Any]) -> str:
    """A core-to-UI push event. It has no `id`, which is how the UI tells it from a response."""
    return json.dumps({"event": name, "data": data})

"""The peer HTTP API (API §5-27, §43). HTTPS only; every route except /health is signed.

No docs/openapi endpoints exist: nothing here is for development use.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import MutableHeaders
from starlette.requests import ClientDisconnect
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from handoff.config import API_VERSION, MAX_JSON_BODY_BYTES, UPLOAD_STALL_TIMEOUT_SECONDS
from handoff.core import Core
from handoff.devices.connection import PLATFORM, ConnectionManager
from handoff.errors import HandOffError
from handoff.peer_api.auth import Authenticator, PeerIdentity, _canonical_uuid
from handoff.peer_api.errors import install_error_handlers
from handoff.transfer.receiver import ReceiverService

_REQUEST_ID = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_TRANSFER_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_PLATFORM = re.compile(r"^[A-Za-z0-9 ._-]{1,32}$")


@dataclass
class PeerContext:
    core: Core
    connections: ConnectionManager
    receiver: ReceiverService
    auth: Authenticator
    # ADR-058: answers a peer's claim on what this device grabbed; None = nothing can be held.
    claims: Callable[[str], dict[str, str]] | None = None


class RequestIdMiddleware:
    """Echo a sane X-Request-ID, or make one (API §35)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        rid = next((v.decode("latin-1") for k, v in scope["headers"] if k == b"x-request-id"), "")
        if not _REQUEST_ID.match(rid):
            rid = f"req_{uuid.uuid4().hex[:16]}"
        scope["handoff_request_id"] = rid

        async def send_with_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)["X-Request-ID"] = rid
            await send(message)

        await self.app(scope, receive, send_with_id)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


async def _read_json_object(request: Request) -> dict[str, Any]:
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_JSON_BODY_BYTES:
        raise HandOffError("INVALID_REQUEST", "Request body is too large.", http_status=413)
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > MAX_JSON_BODY_BYTES:
            raise HandOffError("INVALID_REQUEST", "Request body is too large.", http_status=413)
        chunks.append(chunk)
    try:
        data = json.loads(b"".join(chunks))
    except ValueError as exc:
        raise HandOffError("INVALID_REQUEST", "Request body is not valid JSON.") from exc
    if not isinstance(data, dict):
        raise HandOffError("INVALID_REQUEST", "Request body must be a JSON object.")
    return data


def _parse_connection_body(body: dict[str, Any]) -> tuple[str, str, str, int, str]:
    device_id = body.get("device_id")
    if not isinstance(device_id, str) or _canonical_uuid(device_id) is None:
        raise HandOffError("INVALID_DEVICE_ID", "Invalid device ID.")
    name = body.get("device_name")
    if (
        not isinstance(name, str)
        or not name.strip()
        or len(name) > 64
        or any(ord(c) < 32 for c in name)
    ):
        raise HandOffError("INVALID_REQUEST", "Invalid device name.")
    key = body.get("public_key")
    try:
        raw = base64.b64decode(key, validate=True) if isinstance(key, str) else b""
    except (binascii.Error, ValueError):
        raw = b""
    if len(raw) != 32 or base64.b64encode(raw).decode("ascii") != key:
        raise HandOffError("INVALID_REQUEST", "Invalid public key.")
    port = body.get("port")
    if isinstance(port, bool) or not isinstance(port, int) or not 0 < port < 65536:
        raise HandOffError("INVALID_REQUEST", "Invalid port.")
    platform = body.get("platform", "unknown")
    if not isinstance(platform, str) or not _PLATFORM.match(platform):
        raise HandOffError("INVALID_REQUEST", "Invalid platform.")
    return device_id, name.strip(), str(key), port, platform


def create_app(ctx: PeerContext) -> FastAPI:
    app = FastAPI(title="HandOff peer API", docs_url=None, redoc_url=None, openapi_url=None)
    install_error_handlers(app)
    app.add_middleware(RequestIdMiddleware)

    def authenticate(
        request: Request, *, claimed_key: str | None = None, require_trusted: bool = True
    ) -> PeerIdentity:
        return ctx.auth.authenticate(
            method=request.method,
            path=request.url.path,
            headers=request.headers,
            client_ip=_client_ip(request),
            claimed_key=claimed_key,
            require_trusted=require_trusted,
        )

    @app.get("/api/v1/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "device_id": ctx.core.identity.device_id}

    @app.get("/api/v1/device")
    def device(request: Request) -> dict[str, Any]:
        authenticate(request)
        return {
            "device_id": ctx.core.identity.device_id,
            "device_name": ctx.core.device_name,
            "api_version": API_VERSION,
            "platform": PLATFORM,
            "status": "available",
        }

    @app.post("/api/v1/connection")
    async def connect(request: Request) -> dict[str, Any]:
        try:
            body = await _read_json_object(request)
            device_id, name, key, port, platform = _parse_connection_body(body)
        except HandOffError:
            ctx.auth.limiter.record(_client_ip(request))
            raise
        peer = await run_in_threadpool(
            authenticate, request, claimed_key=key, require_trusted=False
        )
        if peer.device_id != device_id:
            raise HandOffError("INVALID_DEVICE_ID", "Device ID does not match the signature.")
        return await run_in_threadpool(
            ctx.connections.accept_inbound,
            device_id,
            name,
            key,
            _client_ip(request),
            port,
            platform,
        )

    @app.get("/api/v1/connection")
    def get_connection(request: Request) -> dict[str, Any]:
        authenticate(request)
        return ctx.connections.snapshot()

    @app.delete("/api/v1/connection")
    def release_connection(request: Request) -> dict[str, str]:
        peer = authenticate(request)
        ctx.connections.release_inbound(peer.device_id)
        return {"status": "released"}

    @app.post("/api/v1/handoff/claim")
    async def claim_handoff(request: Request) -> JSONResponse:
        # The caller is whoever signed the request; nothing in the body is read or trusted.
        peer = await run_in_threadpool(authenticate, request)
        if ctx.claims is None:
            raise HandOffError("NOTHING_HELD", "This device is not holding anything.")
        result = await run_in_threadpool(ctx.claims, peer.device_id)
        return JSONResponse(result, status_code=202)

    @app.post("/api/v1/transfers")
    async def create_transfer(request: Request) -> JSONResponse:
        peer = await run_in_threadpool(authenticate, request)
        body = await _read_json_object(request)
        key = request.headers.get("idempotency-key")
        result, created = await run_in_threadpool(ctx.receiver.create, peer, body, key)
        return JSONResponse(result, status_code=201 if created else 200)

    @app.post("/api/v1/transfers/{transfer_id}/data")
    async def upload(transfer_id: str, request: Request) -> JSONResponse:
        peer = await run_in_threadpool(authenticate, request)
        ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
        if ctype != "application/zip":
            raise HandOffError(
                "INVALID_REQUEST", "Content-Type must be application/zip.", http_status=415
            )
        job = await run_in_threadpool(ctx.receiver.begin_upload, peer, transfer_id)
        declared = request.headers.get("content-length", "")
        received = 0
        try:
            if declared.isdigit() and int(declared) > job.max_bytes:
                raise HandOffError(
                    "FILE_TOO_LARGE",
                    "The upload is larger than the declared transfer.",
                    http_status=413,
                )
            with job.payload_path.open("wb") as out:
                stream = request.stream().__aiter__()
                while True:
                    try:
                        chunk = await asyncio.wait_for(
                            stream.__anext__(), UPLOAD_STALL_TIMEOUT_SECONDS
                        )
                    except StopAsyncIteration:
                        break
                    received += len(chunk)
                    if received > job.max_bytes:
                        raise HandOffError(
                            "FILE_TOO_LARGE",
                            "The upload is larger than the declared transfer.",
                            http_status=413,
                        )
                    out.write(chunk)
                    job.bytes_received = received
                    if ctx.core.events.progress_due(transfer_id):
                        await run_in_threadpool(
                            ctx.core.events.transfer_changed,
                            transfer_id,
                            bytes_transferred=received,
                            throttle=True,
                        )
            if declared.isdigit() and int(declared) != received:
                raise HandOffError("TRANSFER_INCOMPLETE", "The upload was incomplete.")
        except ClientDisconnect:
            await run_in_threadpool(
                ctx.receiver.fail, job, "CONNECTION_RESET", "The sender disconnected."
            )
            raise HandOffError("CONNECTION_RESET", "The sender disconnected.") from None
        except TimeoutError:
            await run_in_threadpool(
                ctx.receiver.fail, job, "REQUEST_TIMEOUT", "The upload stalled."
            )
            raise HandOffError("REQUEST_TIMEOUT", "The upload stalled.") from None
        except HandOffError as exc:
            await run_in_threadpool(ctx.receiver.fail, job, exc.code, exc.message)
            raise
        except Exception:
            await run_in_threadpool(ctx.receiver.fail, job, "INTERNAL_ERROR", "The upload failed.")
            raise

        summary = await run_in_threadpool(ctx.receiver.process_upload, job)
        if summary["status"] == "failed":
            details = {
                "transfer_id": summary["transfer_id"],
                "status": "failed",
                "files": summary["files"],
            }
            err = HandOffError("TRANSFER_FAILED", "No file passed verification.", details)
            return JSONResponse(err.to_dict(), status_code=422)
        return JSONResponse(summary)

    @app.get("/api/v1/transfers/{transfer_id}")
    def transfer_status(transfer_id: str, request: Request) -> dict[str, Any]:
        peer = authenticate(request)
        if not _TRANSFER_ID.match(transfer_id):
            raise HandOffError("TRANSFER_NOT_FOUND", "Transfer not found.")
        return ctx.receiver.status(peer, transfer_id)

    return app

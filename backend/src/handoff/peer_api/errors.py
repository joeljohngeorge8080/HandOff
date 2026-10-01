"""Standard error responses for the peer API (API §32-34)."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from handoff.errors import HandOffError

log = logging.getLogger(__name__)

HTTP_STATUS: dict[str, int] = {
    "INVALID_REQUEST": 400,
    "INVALID_DEVICE_ID": 400,
    "INVALID_API_VERSION": 400,
    "INVALID_SIGNATURE": 401,
    "REPLAYED_REQUEST": 401,
    "DEVICE_NOT_TRUSTED": 403,
    "DEVICE_NOT_FOUND": 404,
    "FILE_NOT_FOUND": 404,
    "TRANSFER_NOT_FOUND": 404,
    "REQUEST_TIMEOUT": 408,
    "DEVICE_ALREADY_CONNECTED": 409,
    "RECEIVE_MODE_DISABLED": 409,
    "RECEIVER_NOT_READY": 409,
    "INVALID_STATE": 409,
    "TRANSFER_ALREADY_EXISTS": 409,
    "FILE_TOO_LARGE": 413,
    "FILE_TYPE_NOT_SUPPORTED": 422,
    "INVALID_FILE": 422,
    "INVALID_PATH": 422,
    "INVALID_HASH": 422,
    "TRANSFER_FAILED": 422,
    "TRANSFER_INCOMPLETE": 422,
    "TRANSFER_VALIDATION_FAILED": 422,
    "TRANSFER_ARCHIVE_INVALID": 422,
    "TRANSFER_ARCHIVE_EXTRACTION_FAILED": 422,
    "RATE_LIMITED": 429,
    "CONNECTION_RESET": 400,
    "INTERNAL_ERROR": 500,
    "FILE_STORAGE_ERROR": 500,
    "INSUFFICIENT_STORAGE": 507,
}


def status_for(exc: HandOffError) -> int:
    return exc.http_status or HTTP_STATUS.get(exc.code, 400)


def _respond(request: Request, status: int, body: dict[str, object]) -> JSONResponse:
    response = JSONResponse(status_code=status, content=body)
    rid = request.scope.get("handoff_request_id")
    if rid:
        response.headers["X-Request-ID"] = str(rid)
    return response


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(HandOffError)
    async def _app_error(request: Request, exc: HandOffError) -> JSONResponse:
        return _respond(request, status_for(exc), exc.to_dict())

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, _exc: RequestValidationError) -> JSONResponse:
        err = HandOffError("INVALID_REQUEST", "The request is not valid.")
        return _respond(request, 400, err.to_dict())

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        err = HandOffError("INVALID_REQUEST", "No such endpoint or method.")
        return _respond(request, exc.status_code, err.to_dict())

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
        rid = request.scope.get("handoff_request_id")
        log.error("Unhandled error (request %s)", rid, exc_info=exc)
        err = HandOffError("INTERNAL_ERROR", "An unexpected error occurred.")
        return _respond(request, 500, err.to_dict())

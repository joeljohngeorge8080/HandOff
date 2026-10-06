"""Standard application error (API.md §32). Codes come from API.md §33."""

from __future__ import annotations

from typing import Any


class HandOffError(Exception):
    """An expected, user- or peer-facing failure with a stable machine-readable code."""

    def __init__(
        self,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
        http_status: int | None = None,
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.details: dict[str, Any] = details or {}
        self.http_status = http_status  # overrides the code's default HTTP status

    def to_dict(self) -> dict[str, Any]:
        err: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            err["details"] = self.details
        return {"error": err}


class DatabaseInitError(HandOffError):
    """The database could not be opened or has an unusable schema (DATABASE §49)."""

    def __init__(self, message: str) -> None:
        super().__init__("INTERNAL_ERROR", message)


class IdentityError(HandOffError):
    """The persistent device identity is unreadable. Never silently regenerated."""

    def __init__(self, message: str) -> None:
        super().__init__("INTERNAL_ERROR", message)


def must[T](value: T | None, what: str = "record") -> T:
    """Unwrap a lookup that must succeed; a miss is an internal error, not a silent None."""
    if value is None:
        raise HandOffError("INTERNAL_ERROR", f"Internal error: {what} not found.")
    return value

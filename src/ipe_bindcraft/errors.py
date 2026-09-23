"""Typed error codes and exceptions (SPEC section 6.3).

Every business failure raised inside the service is an ``IbcError`` carrying a
stable machine-readable ``code``. The MCP layer converts these to
``isError=true`` results with structured details.
"""

from __future__ import annotations

from typing import Any

# Stable error codes from SPEC 6.3 (+ a few internal ones kept distinct).
CODES = {
    "REVISION_CONFLICT",
    "DOCUMENT_OWNERSHIP_CONFLICT",
    "DUPLICATE_ID",
    "DANGLING_EDGE",
    "REFERENCED_OBJECT",
    "UNSUPPORTED_TRANSFORM",
    "MANAGED_STRUCTURE_CHANGED",
    "LATEX_FAILED",
    "TEXT_OVERFLOW",
    "FONT_MISSING",
    "ROUTE_NOT_FOUND",
    "PREVIEW_STALE",
    "BRIDGE_UNAVAILABLE",
    "GUI_BUSY",
    "FILE_BUSY",
    "PATH_NOT_ALLOWED",
    "COMMIT_STATUS_UNKNOWN",
    # additional explicit codes used by the implementation
    "VALIDATION",            # schema/semantic validation failure
    "NOT_FOUND",             # unknown document_id/object id
    "UNSUPPORTED_DOCUMENT_FEATURE",
    "BACKEND_CAPABILITY_UNAVAILABLE",
    "OVERLAPPING_SELECTION",
    "REQUEST_ID_REUSED",
    "CONSTRAINT_UNSATISFIABLE",
    "INVALID_OPERATION",
    "SESSION_EXPIRED",
    "INTERNAL",
}


class IbcError(Exception):
    """Business error with a stable code and structured details."""

    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None):
        assert code in CODES, f"unknown error code {code}"
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}


class OpError(IbcError):
    """Error tied to a specific operation index inside a batch."""

    def __init__(self, code: str, message: str, *, op_index: int,
                 object_ids: list[str] | None = None, details: dict[str, Any] | None = None):
        d = dict(details or {})
        d["op_index"] = op_index
        if object_ids:
            d["object_ids"] = object_ids
        super().__init__(code, message, details=d)
        self.op_index = op_index

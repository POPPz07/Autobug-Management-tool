"""
AutoRepro Enterprise — Standardized API Response & Error Envelopes

All API responses must use these helpers. No route should return a raw dict or
a plain Pydantic model directly — always wrap via ok() or err().

Response envelope:
    {
        "data":  <payload>,
        "meta":  { "limit": int, "offset": int, "total": int }   # on list endpoints
    }

Error envelope:
    {
        "error": { "code": "BUG_NOT_FOUND", "message": "Bug with id ... not found" }
    }
"""

from __future__ import annotations

from typing import Any, Generic, Optional, TypeVar

from fastapi import HTTPException, status
from pydantic import BaseModel

T = TypeVar("T")


# ═══════════════════════════════════════════════════════════════════
# ENVELOPE SCHEMAS
# ═══════════════════════════════════════════════════════════════════

class Meta(BaseModel):
    limit:  int
    offset: int
    total:  int


class DataResponse(BaseModel, Generic[T]):
    """Single-item response envelope."""
    data: T


class ListResponse(BaseModel, Generic[T]):
    """Paginated list response envelope."""
    data: list[T]
    meta: Meta


class ErrorDetail(BaseModel):
    code:    str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorDetail


# ═══════════════════════════════════════════════════════════════════
# BUILDER HELPERS  (use these in routes)
# ═══════════════════════════════════════════════════════════════════

def ok(data: Any) -> dict:
    """Wrap a single item in the standard data envelope."""
    return {"data": data}


def ok_list(data: list, *, limit: int, offset: int, total: int) -> dict:
    """Wrap a list + pagination meta in the standard list envelope."""
    return {
        "data": data,
        "meta": {"limit": limit, "offset": offset, "total": total},
    }


def err(code: str, message: str, status_code: int = 400) -> HTTPException:
    """
    Raise an HTTPException with the standard error envelope.

    Usage:
        raise err("BUG_NOT_FOUND", f"Bug {bug_id} not found", 404)
    """
    return HTTPException(
        status_code = status_code,
        detail      = {"error": {"code": code, "message": message}},
    )


# ── Convenience pre-built errors ──────────────────────────────────

def not_found(entity: str, entity_id: Any) -> HTTPException:
    return err(
        f"{entity.upper()}_NOT_FOUND",
        f"{entity.capitalize()} '{entity_id}' not found",
        status.HTTP_404_NOT_FOUND,
    )


def forbidden(reason: str = "Insufficient permissions") -> HTTPException:
    return err("FORBIDDEN", reason, status.HTTP_403_FORBIDDEN)


def conflict(reason: str) -> HTTPException:
    return err("CONFLICT", reason, status.HTTP_409_CONFLICT)


def bad_request(reason: str) -> HTTPException:
    return err("BAD_REQUEST", reason, status.HTTP_400_BAD_REQUEST)


def rate_limited(reason: str = "Rate limit exceeded") -> HTTPException:
    return err("RATE_LIMITED", reason, status.HTTP_429_TOO_MANY_REQUESTS)

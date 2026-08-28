"""
AutoRepro Enterprise — Shared API Dependencies

Provides:
  - RequestContext dataclass: single object carrying user_id, company_id, role
    for use across all route handlers, eliminating repeated JWT parsing.
  - get_request_context(): FastAPI dependency returning a RequestContext.
  - Pagination + filter query-param dependencies.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Annotated, Optional

from fastapi import Depends, Header, Query, Request

from api.auth import CurrentUser, build_company_filter
from db.models import BugSeverity, BugStatus, User, UserRole
from db.models_v2 import ApiKey
from db.session import get_session


# ═══════════════════════════════════════════════════════════════════
# REQUEST CONTEXT
# ═══════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class RequestContext:
    """
    Immutable snapshot of the authenticated caller's identity.

    Injected via Depends(get_request_context) into every route that
    needs to know who is calling and which tenant they belong to.

    Fields:
        user_id    — UUID of the authenticated user
        company_id — UUID of their company (or None for PLATFORM_ADMIN)
        role       — their UserRole enum value
        user       — full User ORM object (for fine-grained checks)
    """
    user_id:    uuid.UUID
    company_id: uuid.UUID | None     # None iff PLATFORM_ADMIN (cross-tenant)
    role:       UserRole
    user:       User                 # full object for assert_same_company etc.

    @property
    def is_platform_admin(self) -> bool:
        return self.role == UserRole.PLATFORM_ADMIN

    @property
    def is_org_admin_or_above(self) -> bool:
        from api.auth import ROLE_LEVEL
        return ROLE_LEVEL.get(self.role, 0) >= ROLE_LEVEL[UserRole.ORG_ADMIN]

    @property
    def is_manager_or_above(self) -> bool:
        from api.auth import ROLE_LEVEL
        return ROLE_LEVEL.get(self.role, 0) >= ROLE_LEVEL[UserRole.MANAGER]

    @property
    def is_supervisor_or_above(self) -> bool:
        from api.auth import ROLE_LEVEL
        return ROLE_LEVEL.get(self.role, 0) >= ROLE_LEVEL[UserRole.SUPERVISOR]


def get_request_context(current_user: CurrentUser) -> RequestContext:
    """
    FastAPI dependency — constructs a RequestContext from the JWT-authenticated user.

    Usage in routes:
        @router.get("/bugs")
        def list_bugs(ctx: Ctx):
            stmt = stmt.where(Bug.company_id == ctx.company_id)
    """
    return RequestContext(
        user_id    = current_user.id,
        company_id = build_company_filter(current_user),   # None for PLATFORM_ADMIN
        role       = current_user.role,
        user       = current_user,
    )


def _hash_api_key(raw_key: str) -> str:
    """SHA-256 hash for API key lookup — mirrors api/api_key_routes.py."""
    return hashlib.sha256(raw_key.encode()).hexdigest()


def get_request_context_with_api_key(
    request:      Request,
    x_api_key:    Annotated[str | None, Header(alias="X-API-Key")] = None,
    db=Depends(get_session),
) -> RequestContext:
    """
    Dual-auth dependency: supports both Bearer JWT and X-API-Key header.

    Resolution order:
      1. If Authorization: Bearer <token> is present → use JWT path (get_request_context)
      2. If X-API-Key: <key> is present → look up in api_keys table
      3. If neither → raise 401

    This allows CI/CD pipelines to authenticate without managing JWTs.
    """
    from fastapi import HTTPException
    from fastapi.security import OAuth2PasswordBearer
    from sqlmodel import select

    # ── Path 1: Bearer JWT ──────────────────────────────────────────
    auth_header = request.headers.get("Authorization", "")
    token = None
    if auth_header.startswith("Bearer "):
        token = auth_header.removeprefix("Bearer ").strip()
    elif request.query_params.get("token"):
        token = request.query_params.get("token")

    if token:
        from api.auth import decode_token
        payload = decode_token(token)
        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(401, detail="Invalid token payload")
        user = db.get(__import__("db.models", fromlist=["User"]).User, uuid.UUID(user_id))
        if not user or user.is_deleted or not user.is_active:
            raise HTTPException(401, detail="User not found or inactive")
        return RequestContext(
            user_id    = user.id,
            company_id = build_company_filter(user),
            role       = user.role,
            user       = user,
        )

    # ── Path 2: API Key ─────────────────────────────────────────────
    if x_api_key:
        key_hash = _hash_api_key(x_api_key)
        stmt = select(ApiKey).where(
            ApiKey.key_hash == key_hash,
            ApiKey.is_active == True,  # noqa: E712
        )
        api_key = db.exec(stmt).first()

        if not api_key:
            raise HTTPException(401, detail="Invalid or revoked API key")

        # Check expiry
        if api_key.expires_at and api_key.expires_at < datetime.now(timezone.utc):
            raise HTTPException(401, detail="API key has expired")

        # Update last_used_at (best-effort; don't fail the request if this fails)
        try:
            api_key.last_used_at = datetime.now(timezone.utc)
            db.commit()
        except Exception:
            pass

        # Load the company's ORG_ADMIN as the effective user for context
        # This is a "machine user" context — full company scope, ORG_ADMIN role
        from db.models import User, Company
        company = db.get(Company, api_key.company_id)
        if not company or company.is_deleted:
            raise HTTPException(403, detail="API key company not found or inactive")

        # Synthesize a minimal User-like object for context
        # Use the created_by_user_id as the actor for audit purposes
        actor_user = db.get(User, api_key.created_by_user_id)
        if not actor_user or not actor_user.is_active:
            raise HTTPException(403, detail="API key creator account is inactive")

        return RequestContext(
            user_id    = actor_user.id,
            company_id = api_key.company_id,
            role       = UserRole.ORG_ADMIN,   # API keys always act at org-admin level
            user       = actor_user,
        )

    # ── Path 3: Neither → reject ────────────────────────────────────
    raise HTTPException(
        status_code=401,
        detail="Authentication required: provide Bearer token or X-API-Key header",
        headers={"WWW-Authenticate": "Bearer"},
    )

def require_ctx_permission(perm: str):
    """
    Like require_permission, but works with API Keys via RequestContext.
    Use this for endpoints that need dual-auth (JWT + API Key) AND permission checks.
    """
    from fastapi import HTTPException
    from api.auth import has_permission

    def dependency(ctx: RequestContext = Depends(get_request_context_with_api_key)):
        if not has_permission(ctx.user, perm):
            raise HTTPException(status_code=403, detail=f"Missing permission: {perm}")
    return dependency


# Reusable type aliases for route signatures
# Ctx uses dual-auth (JWT + API Key) — works for all protected routes
Ctx = Annotated[RequestContext, Depends(get_request_context_with_api_key)]



# ═══════════════════════════════════════════════════════════════════
# PAGINATION DEPENDENCY
# ═══════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Pagination:
    limit:  int
    offset: int


def get_pagination(
    limit:  int = Query(default=20, ge=1,  le=100, description="Max items to return"),
    offset: int = Query(default=0,  ge=0,           description="Items to skip"),
) -> Pagination:
    return Pagination(limit=limit, offset=offset)


Page = Annotated[Pagination, Depends(get_pagination)]


# ═══════════════════════════════════════════════════════════════════
# BUG FILTER DEPENDENCY
# ═══════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class BugFilters:
    status:      Optional[BugStatus]   = None
    severity:    Optional[BugSeverity] = None
    assigned_to: Optional[uuid.UUID]   = None
    created_by:  Optional[uuid.UUID]   = None
    team_id:     Optional[uuid.UUID]   = None


def get_bug_filters(
    status:      Optional[BugStatus]   = Query(default=None, description="Filter by bug lifecycle status"),
    severity:    Optional[BugSeverity] = Query(default=None, description="Filter by severity"),
    assigned_to: Optional[uuid.UUID]   = Query(default=None, description="Filter by assigned user UUID"),
    created_by:  Optional[uuid.UUID]   = Query(default=None, description="Filter by reporter UUID"),
    team_id:     Optional[uuid.UUID]   = Query(default=None, description="Filter by team UUID"),
) -> BugFilters:
    return BugFilters(
        status      = status,
        severity    = severity,
        assigned_to = assigned_to,
        created_by  = created_by,
        team_id     = team_id,
    )


BugFilter = Annotated[BugFilters, Depends(get_bug_filters)]

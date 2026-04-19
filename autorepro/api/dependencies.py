"""
AutoRepro Enterprise — Shared API Dependencies

Provides:
  - RequestContext dataclass: single object carrying user_id, company_id, role
    for use across all route handlers, eliminating repeated JWT parsing.
  - get_request_context(): FastAPI dependency returning a RequestContext.
  - Pagination + filter query-param dependencies.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Annotated, Optional

from fastapi import Depends, Query

from api.auth import CurrentUser, build_company_filter
from db.models import BugSeverity, BugStatus, User, UserRole


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


# Reusable type alias for route signatures
Ctx = Annotated[RequestContext, Depends(get_request_context)]


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

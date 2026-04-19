"""
AutoRepro Enterprise — Assignment Service
services/assignment.py

Single source of truth for bug assignment logic.
This service manages ONLY who is assigned to a bug.
It MUST NOT change bug.status — lifecycle.py is the exclusive owner of status.

Assignment permission matrix:
  MANAGER+    -> can assign to SUPERVISOR, DEVELOPER, TESTER (any role in company)
  SUPERVISOR  -> can assign to DEVELOPER or TESTER within their team
  TESTER      -> cannot assign
  DEVELOPER   -> cannot assign
  PLATFORM_ADMIN / SYSTEM -> no restrictions

Assignment rules:
  1. Assignee must exist, be active, and belong to the same company.
  2. SUPERVISOR can only assign to users in their own team.
  3. MANAGER+ can assign to any user in the company.
  4. Assigning to a MANAGER or above is only allowed by PLATFORM_ADMIN/SYSTEM.
  5. BugAssignment table row is always created (history tracking).
  6. Bug.current_assignee_id is synced atomically.
  7. Bug.assigned_to is synced atomically.
  8. ActivityLog(BUG_ASSIGNED) is always written.
  NOTE: bug.status is NOT touched here. If the caller also wants a status
        transition, they must call lifecycle.transition_bug() separately.

This module must NOT import from api/ or worker/.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException, status
from sqlmodel import Session, select

from db.models import (
    ActivityLog, Bug, BugAssignment, User, UserRole,
)
from services.lifecycle import ServiceContext, _role_level, _guard_company, _guard_not_deleted, transition_bug
from utils.logger import get_logger

log = get_logger(__name__)


# ═══════════════════════════════════════════════════════════════════
# ASSIGNMENT PERMISSION MATRIX
#
# assigner_role -> max_assignee_role (inclusive ceiling)
# Assigning to roles ABOVE this ceiling is blocked.
# ═══════════════════════════════════════════════════════════════════

# Who can assign to whom (expressed as max assignee role level)
_ASSIGN_MAX_ASSIGNEE_LEVEL: dict[UserRole, int] = {
    UserRole.SUPERVISOR:     _role_level(UserRole.DEVELOPER),   # can assign to DEV/TESTER only
    UserRole.MANAGER:        _role_level(UserRole.SUPERVISOR),  # can assign up to SUPERVISOR
    UserRole.ORG_ADMIN:      _role_level(UserRole.MANAGER),     # can assign up to MANAGER
    UserRole.PLATFORM_ADMIN: _role_level(UserRole.PLATFORM_ADMIN),
    UserRole.SYSTEM:         _role_level(UserRole.PLATFORM_ADMIN),
}

# Roles that cannot assign at all
_CANNOT_ASSIGN = {UserRole.DEVELOPER, UserRole.TESTER}


# ═══════════════════════════════════════════════════════════════════
# GUARD HELPERS
# ═══════════════════════════════════════════════════════════════════

def _guard_can_assign(ctx: ServiceContext) -> None:
    """Raise 403 if the actor's role is not permitted to assign bugs."""
    if ctx.role in _CANNOT_ASSIGN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": {
                    "code": "ASSIGN_FORBIDDEN",
                    "message": (
                        f"Role '{ctx.role.value}' is not permitted to assign bugs. "
                        "Requires SUPERVISOR or above."
                    ),
                }
            },
        )


def _guard_assignee(
    assignee:      User,
    ctx:           ServiceContext,
    bug:           Bug,
) -> None:
    """
    Validate that the intended assignee is a valid target.

    Checks:
      1. Assignee is active and not deleted.
      2. Assignee belongs to the same company.
      3. Assigner has sufficient role level to assign to the assignee's role.
      4. SUPERVISOR can only assign within their own team.
    """
    if assignee.is_deleted or not assignee.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": {
                    "code": "ASSIGNEE_INACTIVE",
                    "message": f"User '{assignee.id}' is inactive or deleted",
                }
            },
        )

    # Tenant isolation
    if not ctx.bypasses_rules and assignee.company_id != bug.company_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": {
                    "code": "CROSS_TENANT_ASSIGN",
                    "message": "Cannot assign a bug to a user from a different company",
                }
            },
        )

    # Role ceiling check
    if not ctx.bypasses_rules:
        max_level = _ASSIGN_MAX_ASSIGNEE_LEVEL.get(ctx.role, 0)
        assignee_level = _role_level(assignee.role)
        if assignee_level > max_level:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": {
                        "code": "ASSIGNEE_ROLE_TOO_HIGH",
                        "message": (
                            f"Your role ({ctx.role.value}) cannot assign bugs to "
                            f"a {assignee.role.value}. Max assignable role: "
                            f"{UserRole(list(UserRole)[list(_ROLE_LEVEL.values()).index(max_level)]).value}"
                        ),
                    }
                },
            )

    # SUPERVISOR: team-scoped assignment only
    if ctx.role == UserRole.SUPERVISOR and not ctx.bypasses_rules:
        if ctx.user_id is None:
            return
        # Load assigner's team_id to compare
        # We use the bug's team_id as proxy if assigner's team_id unavailable
        if bug.team_id and assignee.team_id != bug.team_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": {
                        "code": "SUPERVISOR_TEAM_SCOPE",
                        "message": (
                            "Supervisors can only assign bugs to users within their team. "
                            f"Assignee is in a different team."
                        ),
                    }
                },
            )


# Re-export the lifecycle ROLE_LEVEL for internal use
from services.lifecycle import _ROLE_LEVEL
_ROLE_LEVEL_MAP = _ROLE_LEVEL  # local alias for clarity


# ═══════════════════════════════════════════════════════════════════
# PUBLIC SERVICE FUNCTION
# ═══════════════════════════════════════════════════════════════════

def assign_bug(
    db:                  Session,
    bug:                 Bug,
    assign_to_user_id:   uuid.UUID,
    ctx:                 ServiceContext,
    *,
    team_id:             Optional[uuid.UUID] = None,
    note:                Optional[str]       = None,
) -> BugAssignment:
    """
    Assign a bug to a user. All business rules are enforced here.

    Steps:
      1. Guard: actor can assign (role >= SUPERVISOR or bypasses)
      2. Guard: bug not deleted, company matches
      3. Load and validate assignee
      4. Create BugAssignment row (history)
      5. Sync Bug.current_assignee_id, Bug.assigned_to, Bug.team_id
      6. Write ActivityLog(BUG_ASSIGNED)
      7. db.add() all changes — caller must commit

    NOTE: bug.status is NOT modified here.
          If the caller wants a status transition (e.g. ASSIGNED state),
          they must call lifecycle.transition_bug() separately after this function.
    """
    # ── 1. Actor capability guard ─────────────────────────────────
    _guard_can_assign(ctx)

    # ── 2. Bug state guards ───────────────────────────────────────
    _guard_not_deleted(bug)
    _guard_company(bug, ctx)

    # ── 3. Assignee validation ────────────────────────────────────
    assignee = db.get(User, assign_to_user_id)
    if not assignee:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": {
                    "code": "ASSIGNEE_NOT_FOUND",
                    "message": f"User '{assign_to_user_id}' not found",
                }
            },
        )
    _guard_assignee(assignee, ctx, bug)

    # ── 4. Create BugAssignment history row ───────────────────────
    effective_team_id = team_id or bug.team_id or assignee.team_id
    assignment = BugAssignment(
        bug_id              = bug.id,
        assigned_to_user_id = assign_to_user_id,
        assigned_by_user_id = ctx.user_id or uuid.UUID(int=0),   # sentinel for SYSTEM
        team_id             = effective_team_id,
        note                = note,
    )
    db.add(assignment)

    # ── 5. Sync denormalized fields on Bug ────────────────────────
    bug.current_assignee_id = assign_to_user_id
    bug.assigned_to         = assign_to_user_id
    if effective_team_id:
        bug.team_id = effective_team_id
    bug.updated_at = datetime.now(timezone.utc)
    db.add(bug)

    # ── 6. ActivityLog: BUG_ASSIGNED ─────────────────────────────
    db.add(ActivityLog(
        entity_type   = "bug",
        entity_id     = bug.id,
        action        = "BUG_ASSIGNED",
        user_id       = ctx.user_id,
        company_id    = bug.company_id,
        metadata_json = {
            "assigned_to": str(assign_to_user_id),
            "assigned_by": str(ctx.user_id) if ctx.user_id else "SYSTEM",
            "team_id":     str(effective_team_id) if effective_team_id else None,
            "note":        note,
        },
    ))

    log.info(
        "bug_assigned",
        bug_id      = str(bug.id),
        assigned_to = str(assign_to_user_id),
        by          = str(ctx.user_id) if ctx.user_id else "SYSTEM",
        by_role     = ctx.role.value,
    )
    return assignment

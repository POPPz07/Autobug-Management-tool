"""
AutoRepro Enterprise — Lifecycle Service
services/lifecycle.py

Single source of truth for ALL bug status transitions.
Only this file may write to bug.status.

Full transition table (user-settable unless noted):
  CREATED            -> TRIAGED             SUPERVISOR+
  TRIAGED            -> ASSIGNED            SUPERVISOR+
  ASSIGNED           -> IN_PROGRESS         DEVELOPER+
  IN_PROGRESS        -> RUNNING_AUTOREPRO   SYSTEM only  (set by job trigger)
  RUNNING_AUTOREPRO  -> RESOLVED            SYSTEM only  (set by worker on success)
  RUNNING_AUTOREPRO  -> IN_PROGRESS         SYSTEM only  (set by worker on failure)
  IN_PROGRESS        -> RESOLVED            DEVELOPER+   (manual resolution)
  RESOLVED           -> CLOSED              MANAGER+
  RESOLVED           -> IN_PROGRESS         TESTER+      (reopen: needs more work)
  CLOSED             -> TRIAGED             TESTER+      (reopen: reactivate)
  Any valid state    -> DUPLICATE           SUPERVISOR+

  PLATFORM_ADMIN and SYSTEM bypass all validation.

Callers:
  - api/bug_routes.py -> transition_bug() [human transitions]
  - services/job_trigger.py -> mark_running_autorepro() [job trigger]
  - worker/runner.py -> mark_resolved(), mark_back_to_in_progress() [job completion]

This module must NOT import from api/ or worker/.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException, status
from sqlmodel import Session

from db.models import (
    ActivityLog, Bug, BugStatus, UserRole,
)
from utils.logger import get_logger

log = get_logger(__name__)


# ═══════════════════════════════════════════════════════════════════
# CALLER CONTEXT
# ═══════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class ServiceContext:
    """
    Minimal context for service-layer operations.
    Decoupled from FastAPI's RequestContext to allow worker calls.

    Fields:
        user_id    — UUID of the acting user (or None for SYSTEM)
        company_id — UUID of the tenant (or None for PLATFORM_ADMIN/SYSTEM)
        role       — UserRole of the actor
    """
    user_id:    Optional[uuid.UUID]
    company_id: Optional[uuid.UUID]
    role:       UserRole

    @property
    def is_system(self) -> bool:
        return self.role == UserRole.SYSTEM

    @property
    def is_platform_admin(self) -> bool:
        return self.role == UserRole.PLATFORM_ADMIN

    @property
    def bypasses_rules(self) -> bool:
        """SYSTEM and PLATFORM_ADMIN skip normal transition validation."""
        return self.role in (UserRole.SYSTEM, UserRole.PLATFORM_ADMIN)


# Singleton context for worker-initiated transitions
SYSTEM_CTX = ServiceContext(
    user_id    = None,
    company_id = None,
    role       = UserRole.SYSTEM,
)


# ═══════════════════════════════════════════════════════════════════
# ROLE LEVEL MAP  (duplicated here to keep services independent of api/)
# ═══════════════════════════════════════════════════════════════════

_ROLE_LEVEL: dict[UserRole, int] = {
    UserRole.DEVELOPER:      1,
    UserRole.TESTER:         2,
    UserRole.SUPERVISOR:     3,
    UserRole.MANAGER:        4,
    UserRole.ORG_ADMIN:      5,
    UserRole.PLATFORM_ADMIN: 6,
    UserRole.SYSTEM:         7,
}


def _role_level(role: UserRole) -> int:
    return _ROLE_LEVEL.get(role, 0)


# ═══════════════════════════════════════════════════════════════════
# LIFECYCLE TRANSITION TABLE
#
# Format:
#   target_status -> TransitionRule(allowed_from, min_role, user_settable)
#
# user_settable=False means only SYSTEM/PLATFORM_ADMIN can set this status.
# ═══════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class TransitionRule:
    """
    Defines what is required to make a specific status transition.

    Fields:
        allowed_from   — set of statuses the bug must currently be in
        min_role       — minimum UserRole required to make this transition
        user_settable  — False: ONLY SYSTEM or PLATFORM_ADMIN can set this status
        system_only    — True: SYSTEM role is the ONLY role that can set this
                         (PLATFORM_ADMIN can still override via bypasses_rules)
    """
    allowed_from:   frozenset[BugStatus]
    min_role:       UserRole
    user_settable:  bool = True    # False = blocked to direct user transitions
    system_only:    bool = False   # True = SYSTEM role is the sole setter


_TRANSITION_TABLE: dict[BugStatus, TransitionRule] = {
    # ── Normal forward flow ───────────────────────────────────────
    #
    # TRIAGED: two valid source states
    #   CREATED -> TRIAGED : normal triage (SUPERVISOR+)
    #   CLOSED  -> TRIAGED : reopen a closed bug (TESTER+)
    # min_role=TESTER covers both; SUPERVISOR+ always satisfies TESTER level.
    BugStatus.TRIAGED: TransitionRule(
        allowed_from  = frozenset({BugStatus.CREATED, BugStatus.CLOSED}),
        min_role      = UserRole.TESTER,
    ),
    BugStatus.ASSIGNED: TransitionRule(
        allowed_from  = frozenset({BugStatus.TRIAGED}),
        min_role      = UserRole.SUPERVISOR,
    ),
    BugStatus.IN_PROGRESS: TransitionRule(
        # Human: ASSIGNED -> IN_PROGRESS (developer picks it up)
        # Human: RESOLVED -> IN_PROGRESS (reopen — TESTER pulls back for more work)
        # System: RUNNING_AUTOREPRO -> IN_PROGRESS (worker failure fallback, bypasses)
        allowed_from  = frozenset({
            BugStatus.ASSIGNED,
            BugStatus.RESOLVED,            # reopen: TESTER+ needs more work
            BugStatus.RUNNING_AUTOREPRO,   # SYSTEM-only path (failure fallback)
        }),
        min_role      = UserRole.TESTER,   # lowest role that can trigger any of these
    ),
    BugStatus.RUNNING_AUTOREPRO: TransitionRule(
        # SYSTEM-only: triggered exclusively via job trigger path
        allowed_from  = frozenset({BugStatus.IN_PROGRESS}),
        min_role      = UserRole.SYSTEM,    # only SYSTEM or PLATFORM_ADMIN (bypasses)
        user_settable = False,              # blocked to all direct user transitions
        system_only   = True,
    ),
    BugStatus.RESOLVED: TransitionRule(
        # System success: RUNNING_AUTOREPRO -> RESOLVED  (system_only path, bypasses)
        # Manual:         IN_PROGRESS -> RESOLVED         (developer)
        allowed_from  = frozenset({
            BugStatus.RUNNING_AUTOREPRO,   # SYSTEM-only path (job success)
            BugStatus.IN_PROGRESS,         # manual resolution by developer
        }),
        min_role      = UserRole.DEVELOPER,
    ),
    BugStatus.CLOSED: TransitionRule(
        allowed_from  = frozenset({BugStatus.RESOLVED}),
        min_role      = UserRole.MANAGER,
    ),
    # ── Side branch ───────────────────────────────────────────────
    BugStatus.DUPLICATE: TransitionRule(
        allowed_from  = frozenset({
            BugStatus.CREATED, BugStatus.TRIAGED, BugStatus.ASSIGNED,
            BugStatus.IN_PROGRESS, BugStatus.RUNNING_AUTOREPRO,
        }),
        min_role      = UserRole.SUPERVISOR,
    ),
}



# ═══════════════════════════════════════════════════════════════════
# GUARD HELPERS
# ═══════════════════════════════════════════════════════════════════

def _guard_company(bug: Bug, ctx: ServiceContext) -> None:
    """Raise 403 if the bug belongs to a different company. SYSTEM bypasses."""
    if ctx.bypasses_rules:
        return
    if ctx.company_id and bug.company_id != ctx.company_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": {"code": "CROSS_TENANT", "message": "Cross-tenant access denied"}},
        )


def _guard_not_deleted(bug: Bug) -> None:
    """Raise 409 if the bug is soft-deleted."""
    if bug.is_deleted:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": {"code": "BUG_DELETED", "message": "Cannot transition a deleted bug"}},
        )


def _guard_transition(
    bug: Bug,
    new_status: BugStatus,
    ctx: ServiceContext,
) -> None:
    """Validate the transition against the table. Raises 400/403 on failure."""
    # SYSTEM and PLATFORM_ADMIN bypass all transition rules
    if ctx.bypasses_rules:
        return

    rule = _TRANSITION_TABLE.get(new_status)
    if rule is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": {
                    "code": "INVALID_TRANSITION_TARGET",
                    "message": f"'{new_status.value}' is not a valid transition target",
                }
            },
        )

    # Block user-unsettable or system-only statuses (e.g. RUNNING_AUTOREPRO)
    if not rule.user_settable:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": {
                    "code": "TRANSITION_FORBIDDEN",
                    "message": (
                        f"Status '{new_status.value}' can only be set by the internal "
                        "execution system. Trigger a job to advance the bug to this state."
                    ),
                }
            },
        )

    # system_only: even if user_settable=True, only SYSTEM can set it
    # (PLATFORM_ADMIN already bypasses above; this guard is for human roles)
    if rule.system_only:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": {
                    "code": "SYSTEM_ONLY_TRANSITION",
                    "message": (
                        f"Status '{new_status.value}' is set exclusively by the "
                        "AutoRepro engine. No human role can transition to it directly."
                    ),
                }
            },
        )

    # Check allowed source status
    if bug.status not in rule.allowed_from:
        allowed = ", ".join(s.value for s in sorted(rule.allowed_from, key=lambda s: s.value))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": {
                    "code": "INVALID_TRANSITION",
                    "message": (
                        f"Cannot transition from '{bug.status.value}' to '{new_status.value}'. "
                        f"Allowed source states: [{allowed}]"
                    ),
                }
            },
        )

    # Check role level
    if _role_level(ctx.role) < _role_level(rule.min_role):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": {
                    "code": "INSUFFICIENT_ROLE",
                    "message": (
                        f"Transition to '{new_status.value}' requires "
                        f"at least {rule.min_role.value} role"
                    ),
                }
            },
        )


# ═══════════════════════════════════════════════════════════════════
# PUBLIC SERVICE FUNCTION
# ═══════════════════════════════════════════════════════════════════

def transition_bug(
    db:         Session,
    bug:        Bug,
    new_status: BugStatus,
    ctx:        ServiceContext,
    *,
    extra_meta: Optional[dict] = None,
) -> Bug:
    """
    Transition a bug to a new lifecycle status.

    This is the ONLY place where bug.status may be changed.
    All callers (API routes, worker, job trigger) must use this function.

    Args:
        db:         Active SQLModel Session (caller must commit)
        bug:        The Bug ORM object to update
        new_status: Target BugStatus
        ctx:        ServiceContext identifying the actor
        extra_meta: Optional additional metadata merged into the ActivityLog

    Returns:
        The updated Bug object (not yet committed; caller commits)

    Raises:
        HTTPException(400) — invalid transition
        HTTPException(403) — insufficient role or cross-tenant
        HTTPException(409) — bug is soft-deleted
    """
    _guard_not_deleted(bug)
    _guard_company(bug, ctx)
    _guard_transition(bug, new_status, ctx)

    old_status = bug.status
    bug.status     = new_status
    bug.updated_at = datetime.now(timezone.utc)
    db.add(bug)

    meta: dict = {"from": old_status.value, "to": new_status.value}
    if extra_meta:
        meta.update(extra_meta)

    db.add(ActivityLog(
        entity_type   = "bug",
        entity_id     = bug.id,
        action        = "STATUS_CHANGED",
        user_id       = ctx.user_id,
        company_id    = bug.company_id,
        metadata_json = meta,
    ))

    log.info(
        "lifecycle_transition",
        bug_id     = str(bug.id),
        from_state = old_status.value,
        to_state   = new_status.value,
        by_role    = ctx.role.value,
        by_user    = str(ctx.user_id) if ctx.user_id else "SYSTEM",
    )
    return bug


# ═══════════════════════════════════════════════════════════════════
# WORKER-SPECIFIC HELPERS
# Called by worker/runner.py only — use SYSTEM_CTX
# ═══════════════════════════════════════════════════════════════════

def mark_running_autorepro(db: Session, bug: Bug) -> Bug:
    """
    Worker calls this when a job starts executing.
    Transitions: IN_PROGRESS -> RUNNING_AUTOREPRO
    No role check needed — SYSTEM_CTX bypasses.
    """
    return transition_bug(db, bug, BugStatus.RUNNING_AUTOREPRO, SYSTEM_CTX)


def mark_resolved(db: Session, bug: Bug) -> Bug:
    """
    Worker calls this when a job succeeds (reproducibility confirmed).
    Transitions: RUNNING_AUTOREPRO -> RESOLVED
    """
    return transition_bug(db, bug, BugStatus.RESOLVED, SYSTEM_CTX)


def mark_back_to_in_progress(
    db:             Session,
    bug:            Bug,
    *,
    failure_reason: Optional[str] = None,
) -> Bug:
    """
    Worker calls this when a job fails.
    Transitions: RUNNING_AUTOREPRO -> IN_PROGRESS
    Includes failure_reason in ActivityLog metadata for traceability.
    """
    return transition_bug(
        db, bug, BugStatus.IN_PROGRESS, SYSTEM_CTX,
        extra_meta={"reason": failure_reason or "job_failed"},
    )


# ═══════════════════════════════════════════════════════════════════
# UTILITIES
# ═══════════════════════════════════════════════════════════════════

def get_allowed_next_states(current_status: BugStatus, role: UserRole) -> list[BugStatus]:
    """
    Return the list of states this role could transition to from current_status.
    Useful for the frontend to render a context-aware transition button.
    """
    allowed = []
    for target, rule in _TRANSITION_TABLE.items():
        if current_status not in rule.allowed_from:
            continue
        if not rule.user_settable:
            continue
        if _role_level(role) < _role_level(rule.min_role):
            continue
        allowed.append(target)
    return allowed

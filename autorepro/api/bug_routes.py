"""
AutoRepro Enterprise — Bug Routes  /api/v1/bugs/

Routes are thin: they validate HTTP input, call services, and return responses.
Zero business logic lives here.

Endpoints:
  POST   /api/v1/bugs/                      Create bug
  GET    /api/v1/bugs/                      List bugs (paginated + filtered)
  GET    /api/v1/bugs/{bug_id}              Get single bug
  PATCH  /api/v1/bugs/{bug_id}             Update bug fields
  DELETE /api/v1/bugs/{bug_id}             Soft-delete bug (MANAGER+)
  POST   /api/v1/bugs/{bug_id}/assign       Assign bug (calls assignment service)
  PATCH  /api/v1/bugs/{bug_id}/transition   Lifecycle transition (calls lifecycle service)
  GET    /api/v1/bugs/{bug_id}/history      Assignment history
  GET    /api/v1/bugs/{bug_id}/jobs         Execution job history
  GET    /api/v1/bugs/{bug_id}/transitions  What transitions are available to caller
"""

import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel as _BM
from sqlmodel import Session, select, func

from api.auth import (
    Perm,
    assert_same_company, assert_team_access,
    require_min_role, require_permission,
)
from api.dependencies import BugFilter, Ctx, Page
from api.responses import (
    bad_request, forbidden, not_found, ok, ok_list,
)
from db.models import (
    Bug, BugAssignment, BugAssignmentCreate, BugAssignmentPublic,
    BugCreate, BugPublic, BugStatus, BugUpdate,
    Job, JobPublic,
    ActivityLog,
    UserRole,
)
from db.session import SessionDep
from services.assignment import assign_bug as svc_assign_bug
from services.lifecycle import (
    ServiceContext, get_allowed_next_states, transition_bug as svc_transition,
)
from utils.logger import get_logger

log = get_logger(__name__)

bug_router = APIRouter(prefix="/api/v1/bugs", tags=["bugs"])


# ── Helpers ───────────────────────────────────────────────────────

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _get_bug_or_404(session: Session, bug_id: uuid.UUID) -> Bug:
    bug = session.get(Bug, bug_id)
    if not bug or bug.is_deleted:
        raise not_found("bug", bug_id)
    return bug


def _ctx_to_service(ctx: Ctx) -> ServiceContext:
    """Convert API RequestContext -> services ServiceContext."""
    return ServiceContext(
        user_id    = ctx.user_id,
        company_id = ctx.company_id,
        role       = ctx.role,
    )


# ═══════════════════════════════════════════════════════════════════
# CREATE BUG
# ═══════════════════════════════════════════════════════════════════

@bug_router.post("", status_code=201)
def create_bug(
    body:    BugCreate,
    session: SessionDep,
    ctx:     Ctx,
    _:       None = Depends(require_permission(Perm.BUG_CREATE)),
):
    """Create a new bug report."""
    bug = Bug(
        title               = body.title,
        description         = body.description,
        target_url          = body.target_url,
        priority            = body.priority,
        severity            = body.severity,
        environment         = body.environment,
        browser             = body.browser,
        os                  = body.os,
        device              = body.device,
        team_id             = body.team_id,
        status              = BugStatus.CREATED,
        created_by_user_id  = ctx.user_id,
        reported_by         = ctx.user_id,
        company_id          = ctx.user.company_id,
    )
    session.add(bug)
    session.flush()

    session.add(ActivityLog(
        entity_type   = "bug",
        entity_id     = bug.id,
        action        = "BUG_CREATED",
        user_id       = ctx.user_id,
        company_id    = ctx.user.company_id,
        metadata_json = {"title": bug.title},
    ))
    session.commit()
    session.refresh(bug)

    log.info("bug_created", bug_id=str(bug.id), by=str(ctx.user_id))
    return ok(BugPublic.model_validate(bug))


# ═══════════════════════════════════════════════════════════════════
# LIST BUGS
# ═══════════════════════════════════════════════════════════════════

@bug_router.get("")
def list_bugs(
    session: SessionDep,
    ctx:     Ctx,
    page:    Page,
    filters: BugFilter,
    _:       None = Depends(require_permission(Perm.BUG_READ)),
):
    base = select(Bug).where(Bug.is_deleted == False)  # noqa: E712

    if ctx.company_id:
        base = base.where(Bug.company_id == ctx.company_id)

    if not ctx.is_supervisor_or_above and ctx.user.team_id:
        base = base.where(Bug.team_id == ctx.user.team_id)

    if filters.status:
        base = base.where(Bug.status == filters.status)
    if filters.severity:
        base = base.where(Bug.severity == filters.severity)
    if filters.assigned_to:
        base = base.where(Bug.current_assignee_id == filters.assigned_to)
    if filters.created_by:
        base = base.where(Bug.created_by_user_id == filters.created_by)
    if filters.team_id:
        base = base.where(Bug.team_id == filters.team_id)

    total = session.exec(select(func.count()).select_from(base.subquery())).one()
    bugs  = session.exec(
        base.order_by(Bug.created_at.desc())
            .offset(page.offset)
            .limit(page.limit)
    ).all()

    return ok_list(
        [BugPublic.model_validate(b) for b in bugs],
        limit=page.limit, offset=page.offset, total=total,
    )


# ═══════════════════════════════════════════════════════════════════
# GET SINGLE BUG
# ═══════════════════════════════════════════════════════════════════

@bug_router.get("/{bug_id}")
def get_bug(
    bug_id:  uuid.UUID,
    session: SessionDep,
    ctx:     Ctx,
    _:       None = Depends(require_permission(Perm.BUG_READ)),
):
    bug = _get_bug_or_404(session, bug_id)
    assert_same_company(ctx.user, bug.company_id)
    assert_team_access(ctx.user, bug.team_id)
    return ok(BugPublic.model_validate(bug))


# ═══════════════════════════════════════════════════════════════════
# UPDATE BUG FIELDS  (metadata only — not status)
# ═══════════════════════════════════════════════════════════════════

@bug_router.patch("/{bug_id}")
def update_bug(
    bug_id:  uuid.UUID,
    body:    BugUpdate,
    session: SessionDep,
    ctx:     Ctx,
    _:       None = Depends(require_permission(Perm.BUG_UPDATE)),
):
    """Update bug metadata. Status cannot be changed here — use /transition."""
    bug = _get_bug_or_404(session, bug_id)
    assert_same_company(ctx.user, bug.company_id)

    update_data = body.model_dump(exclude_unset=True)

    # Block status from being changed via this endpoint
    update_data.pop("status", None)

    for key, value in update_data.items():
        setattr(bug, key, value)
    bug.updated_at = _utcnow()
    session.add(bug)
    session.commit()
    session.refresh(bug)
    return ok(BugPublic.model_validate(bug))


# ═══════════════════════════════════════════════════════════════════
# SOFT DELETE BUG
# ═══════════════════════════════════════════════════════════════════

@bug_router.delete("/{bug_id}", status_code=204)
def delete_bug(
    bug_id:  uuid.UUID,
    session: SessionDep,
    ctx:     Ctx,
    _:       None = Depends(require_permission(Perm.BUG_DELETE)),
):
    bug = _get_bug_or_404(session, bug_id)
    assert_same_company(ctx.user, bug.company_id)

    bug.is_deleted = True
    bug.updated_at = _utcnow()
    session.add(bug)
    session.add(ActivityLog(
        entity_type   = "bug",
        entity_id     = bug.id,
        action        = "BUG_DELETED",
        user_id       = ctx.user_id,
        company_id    = bug.company_id,
    ))
    session.commit()
    log.info("bug_soft_deleted", bug_id=str(bug_id), by=str(ctx.user_id))


# ═══════════════════════════════════════════════════════════════════
# ASSIGN BUG  — delegates to assignment service
# ═══════════════════════════════════════════════════════════════════

@bug_router.post("/{bug_id}/assign")
def assign_bug_route(
    bug_id:  uuid.UUID,
    body:    BugAssignmentCreate,
    session: SessionDep,
    ctx:     Ctx,
    _:       None = Depends(require_permission(Perm.BUG_ASSIGN)),
):
    """
    Assign a bug to a user.
    All business logic is in services/assignment.py.
    """
    bug = _get_bug_or_404(session, bug_id)
    assert_same_company(ctx.user, bug.company_id)

    svc_ctx    = _ctx_to_service(ctx)
    assignment = svc_assign_bug(
        db                = session,
        bug               = bug,
        assign_to_user_id = body.assigned_to_user_id,
        ctx               = svc_ctx,
        team_id           = body.team_id,
        note              = body.note,
    )
    session.commit()
    session.refresh(assignment)
    return ok(BugAssignmentPublic.model_validate(assignment))


# ═══════════════════════════════════════════════════════════════════
# LIFECYCLE TRANSITION  — delegates to lifecycle service
# ═══════════════════════════════════════════════════════════════════

class TransitionRequest(_BM):
    new_status: BugStatus


@bug_router.patch("/{bug_id}/transition")
def transition_bug_route(
    bug_id:  uuid.UUID,
    body:    TransitionRequest,
    session: SessionDep,
    ctx:     Ctx,
    _:       None = Depends(require_permission(Perm.BUG_UPDATE)),
):
    """
    Move a bug to a new lifecycle status.
    All validation is in services/lifecycle.py (transition table + role check).
    """
    bug    = _get_bug_or_404(session, bug_id)
    assert_same_company(ctx.user, bug.company_id)

    svc_ctx = _ctx_to_service(ctx)
    svc_transition(session, bug, body.new_status, svc_ctx)
    session.commit()
    session.refresh(bug)
    return ok(BugPublic.model_validate(bug))


# ═══════════════════════════════════════════════════════════════════
# AVAILABLE TRANSITIONS  (helper endpoint for the frontend)
# ═══════════════════════════════════════════════════════════════════

@bug_router.get("/{bug_id}/transitions")
def get_available_transitions(
    bug_id:  uuid.UUID,
    session: SessionDep,
    ctx:     Ctx,
    _:       None = Depends(require_permission(Perm.BUG_READ)),
):
    """
    Return the list of statuses the caller can transition this bug to.
    Frontend uses this to render context-aware buttons.
    """
    bug     = _get_bug_or_404(session, bug_id)
    assert_same_company(ctx.user, bug.company_id)

    allowed = get_allowed_next_states(bug.status, ctx.role)
    return ok({
        "current_status":    bug.status.value,
        "available_next":    [s.value for s in allowed],
    })


# ═══════════════════════════════════════════════════════════════════
# ASSIGNMENT HISTORY
# ═══════════════════════════════════════════════════════════════════

@bug_router.get("/{bug_id}/history")
def get_assignment_history(
    bug_id:  uuid.UUID,
    session: SessionDep,
    ctx:     Ctx,
    page:    Page,
    _:       None = Depends(require_permission(Perm.BUG_READ)),
):
    bug = _get_bug_or_404(session, bug_id)
    assert_same_company(ctx.user, bug.company_id)

    stmt = (
        select(BugAssignment)
        .where(BugAssignment.bug_id == bug_id)
        .order_by(BugAssignment.created_at.desc())
    )
    total = session.exec(select(func.count()).select_from(stmt.subquery())).one()
    items = session.exec(stmt.offset(page.offset).limit(page.limit)).all()

    return ok_list(
        [BugAssignmentPublic.model_validate(a) for a in items],
        limit=page.limit, offset=page.offset, total=total,
    )


# ═══════════════════════════════════════════════════════════════════
# EXECUTION JOB HISTORY
# ═══════════════════════════════════════════════════════════════════

@bug_router.get("/{bug_id}/jobs")
def get_bug_jobs(
    bug_id:  uuid.UUID,
    session: SessionDep,
    ctx:     Ctx,
    page:    Page,
    _:       None = Depends(require_permission(Perm.JOB_READ)),
):
    bug = _get_bug_or_404(session, bug_id)
    assert_same_company(ctx.user, bug.company_id)

    stmt = (
        select(Job)
        .where(Job.bug_id == bug_id)
        .order_by(Job.created_at.desc())
    )
    total = session.exec(select(func.count()).select_from(stmt.subquery())).one()
    jobs  = session.exec(stmt.offset(page.offset).limit(page.limit)).all()

    return ok_list(
        [JobPublic.model_validate(j) for j in jobs],
        limit=page.limit, offset=page.offset, total=total,
    )

"""
AutoRepro Enterprise — Bulk Operations Routes (V2.0)

Batch assign, status update, and soft delete for multiple bugs.
Requires SUPERVISOR+ for assign/status, MANAGER+ for delete.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from api.dependencies import Ctx
from api.responses import ok
from db.models import BugStatus
from db.session import get_session
from services.bulk_operations import bulk_assign, bulk_delete, bulk_update_status

router = APIRouter(prefix="/api/v1/bugs/bulk", tags=["bulk"])


class BulkAssignRequest(BaseModel):
    bug_ids: list[UUID]
    assigned_to_user_id: UUID


class BulkStatusRequest(BaseModel):
    bug_ids: list[UUID]
    new_status: BugStatus


class BulkDeleteRequest(BaseModel):
    bug_ids: list[UUID]


@router.post("/assign")
def bulk_assign_endpoint(
    data: BulkAssignRequest,
    ctx: Ctx,
    db: Session = Depends(get_session),
):
    """Assign multiple bugs to one user (SUPERVISOR+)."""
    if not ctx.is_supervisor_or_above:
        raise HTTPException(403, detail="Supervisor role or above required")

    count = bulk_assign(db, data.bug_ids, data.assigned_to_user_id, ctx)
    return ok({"message": f"Assigned {count} bugs"})


@router.post("/status")
def bulk_update_status_endpoint(
    data: BulkStatusRequest,
    ctx: Ctx,
    db: Session = Depends(get_session),
):
    """Update status for multiple bugs (SUPERVISOR+)."""
    if not ctx.is_supervisor_or_above:
        raise HTTPException(403, detail="Supervisor role or above required")

    count = bulk_update_status(db, data.bug_ids, data.new_status, ctx)
    return ok({"message": f"Updated {count} bugs"})


@router.post("/delete")
def bulk_delete_endpoint(
    data: BulkDeleteRequest,
    ctx: Ctx,
    db: Session = Depends(get_session),
):
    """Soft delete multiple bugs (MANAGER+)."""
    if not ctx.is_manager_or_above:
        raise HTTPException(403, detail="Manager role or above required")

    count = bulk_delete(db, data.bug_ids, ctx)
    return ok({"message": f"Deleted {count} bugs"})

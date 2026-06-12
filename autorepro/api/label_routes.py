"""
AutoRepro Enterprise — Label Routes (V2.0)

CRUD for company-scoped labels and adding/removing labels on bugs.
Label creation requires SUPERVISOR role or above.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from api.dependencies import Ctx
from api.responses import ok, ok_list
from db.models import Bug
from db.models_v2 import BugLabel, BugLabelLink
from db.session import get_session

router = APIRouter(prefix="/api/v1/labels", tags=["labels"])


class BugLabelCreate(BaseModel):
    """Request body for creating a label."""
    name: str
    color: str          # hex color e.g. "#FF5733"
    description: str | None = None


@router.post("/")
def create_label(
    data: BugLabelCreate,
    ctx: Ctx,
    db: Session = Depends(get_session),
):
    """Create a new label for the company (SUPERVISOR+ only)."""
    if not ctx.is_supervisor_or_above:
        raise HTTPException(403, detail="Supervisor role or above required")

    label = BugLabel(
        company_id=ctx.company_id,
        name=data.name,
        color=data.color,
        description=data.description,
    )
    db.add(label)
    db.commit()
    db.refresh(label)
    return ok(label)


@router.get("/")
def list_labels(ctx: Ctx, db: Session = Depends(get_session)):
    """List all labels for the current company."""
    stmt = select(BugLabel).where(
        BugLabel.company_id == ctx.company_id,
        BugLabel.is_deleted == False,  # noqa
    )
    labels = db.exec(stmt).all()
    return ok_list(labels, limit=len(labels), offset=0, total=len(labels))


@router.post("/bugs/{bug_id}/labels/{label_id}")
def add_label_to_bug(
    bug_id: UUID,
    label_id: UUID,
    ctx: Ctx,
    db: Session = Depends(get_session),
):
    """Add a label to a bug (idempotent — returns ok if already exists)."""
    bug = db.get(Bug, bug_id)
    label = db.get(BugLabel, label_id)

    if not bug or not label:
        raise HTTPException(404, detail="Bug or label not found")

    # Tenant isolation check
    if bug.company_id != ctx.company_id or label.company_id != ctx.company_id:
        raise HTTPException(404, detail="Bug or label not found")

    # Check if link already exists (idempotent)
    stmt = select(BugLabelLink).where(
        BugLabelLink.bug_id == bug_id,
        BugLabelLink.label_id == label_id,
    )
    if db.exec(stmt).first():
        return ok({"message": "Label already added"})

    link = BugLabelLink(bug_id=bug_id, label_id=label_id)
    db.add(link)
    db.commit()
    return ok(link)


@router.delete("/bugs/{bug_id}/labels/{label_id}")
def remove_label_from_bug(
    bug_id: UUID,
    label_id: UUID,
    ctx: Ctx,
    db: Session = Depends(get_session),
):
    """Remove a label from a bug."""
    stmt = select(BugLabelLink).where(
        BugLabelLink.bug_id == bug_id,
        BugLabelLink.label_id == label_id,
    )
    link = db.exec(stmt).first()

    if link:
        db.delete(link)
        db.commit()

    return ok({"message": "Label removed"})

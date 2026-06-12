"""
AutoRepro Enterprise — Bug Template Routes (V2.0)

CRUD for bug templates and instantiation (creating bugs from templates).
Template creation requires MANAGER role or above.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from api.dependencies import Ctx, Page
from api.responses import ok, ok_list
from db.models import BugPriority, BugSeverity
from db.models_v2 import BugTemplate
from db.session import get_session
from services.templates import create_bug_from_template

router = APIRouter(prefix="/api/v1/templates", tags=["templates"])


# ── Pydantic Schemas ──────────────────────────────────────────────

class BugTemplateCreate(BaseModel):
    """Request body for creating a bug template."""
    name: str
    description_template: str
    default_priority: BugPriority = BugPriority.MEDIUM
    default_severity: BugSeverity = BugSeverity.MEDIUM
    default_environment: str = "production"


class BugFromTemplateRequest(BaseModel):
    """Request body for instantiating a bug from a template."""
    target_url: str
    title: str | None = None
    priority: BugPriority | None = None
    severity: BugSeverity | None = None
    environment: str | None = None
    placeholders: dict[str, str] | None = None


# ── Routes ────────────────────────────────────────────────────────

@router.post("/")
def create_template(
    data: BugTemplateCreate,
    ctx: Ctx,
    db: Session = Depends(get_session),
):
    """Create a new bug template (MANAGER+ only)."""
    if not ctx.is_manager_or_above:
        raise HTTPException(403, detail="Manager role or above required")

    template = BugTemplate(
        company_id=ctx.company_id,
        created_by_user_id=ctx.user_id,
        name=data.name,
        description_template=data.description_template,
        default_priority=data.default_priority,
        default_severity=data.default_severity,
        default_environment=data.default_environment,
    )
    db.add(template)
    db.commit()
    db.refresh(template)

    return ok(template)


@router.get("/")
def list_templates(
    ctx: Ctx,
    db: Session = Depends(get_session),
):
    """List all templates for the current company."""
    stmt = select(BugTemplate).where(
        BugTemplate.company_id == ctx.company_id,
        BugTemplate.is_deleted == False,  # noqa
    )
    templates = db.exec(stmt).all()
    return ok_list(templates, limit=len(templates), offset=0, total=len(templates))


@router.post("/{template_id}/instantiate")
def instantiate_template(
    template_id: UUID,
    data: BugFromTemplateRequest,
    ctx: Ctx,
    db: Session = Depends(get_session),
):
    """Create a bug from a template with placeholder substitution."""
    template = db.get(BugTemplate, template_id)
    if not template or template.is_deleted or template.company_id != ctx.company_id:
        raise HTTPException(404, detail="Template not found")

    overrides = {"target_url": data.target_url}
    if data.title:
        overrides["title"] = data.title
    if data.priority:
        overrides["priority"] = data.priority
    if data.severity:
        overrides["severity"] = data.severity
    if data.environment:
        overrides["environment"] = data.environment
    if data.placeholders:
        overrides["placeholders"] = data.placeholders

    bug = create_bug_from_template(db, template, ctx.user, overrides)
    return ok(bug)

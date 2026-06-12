"""
AutoRepro Enterprise — Platform Admin Routes (V2.0)

Endpoints for PLATFORM_ADMIN users to manage companies, view usage,
impersonate users, and check system health.

All endpoints require PLATFORM_ADMIN role except /health (public).
"""

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, func, select

from api.dependencies import Ctx, Page
from api.responses import ok, ok_list
from db.models import Company, UserRole
from db.session import get_session
from services.platform import create_company, get_system_health, impersonate_user
from services.usage import get_usage_summary

router = APIRouter(prefix="/api/v1/platform", tags=["platform"])


# ── Pydantic Schemas ──────────────────────────────────────────────

class CompanyCreate(BaseModel):
    """Request body for creating a new tenant company."""
    name: str
    slug: str
    plan_id: UUID


# ── Routes ────────────────────────────────────────────────────────

@router.post("/companies")
def create_company_endpoint(
    data: CompanyCreate,
    ctx: Ctx,
    db: Session = Depends(get_session),
):
    """Create a new tenant company (PLATFORM_ADMIN only)."""
    if not ctx.is_platform_admin:
        raise HTTPException(403, detail="Platform admin required")

    company = create_company(db, data.name, data.slug, data.plan_id)
    return ok(company)


@router.get("/companies")
def list_companies(
    ctx: Ctx,
    page: Page,
    db: Session = Depends(get_session),
):
    """List all companies with pagination (PLATFORM_ADMIN only)."""
    if not ctx.is_platform_admin:
        raise HTTPException(403, detail="Platform admin required")

    # Count total
    total_stmt = select(func.count()).select_from(Company).where(Company.is_deleted == False)  # noqa
    total = db.exec(total_stmt).one()

    # Fetch page
    stmt = select(Company).where(Company.is_deleted == False).offset(page.offset).limit(page.limit)  # noqa
    companies = db.exec(stmt).all()

    return ok_list(companies, limit=page.limit, offset=page.offset, total=total)


@router.get("/companies/{company_id}/usage")
def get_company_usage(
    company_id: UUID,
    start_date: date,
    end_date: date,
    ctx: Ctx,
    db: Session = Depends(get_session),
):
    """Get usage report for a company in a date range (PLATFORM_ADMIN only)."""
    if not ctx.is_platform_admin:
        raise HTTPException(403, detail="Platform admin required")

    summary = get_usage_summary(db, company_id, start_date, end_date)
    return ok(summary)


@router.post("/impersonate/{user_id}")
def impersonate_user_endpoint(
    user_id: UUID,
    ctx: Ctx,
    db: Session = Depends(get_session),
):
    """Generate JWT for target user — logged to ActivityLog (PLATFORM_ADMIN only)."""
    if not ctx.is_platform_admin:
        raise HTTPException(403, detail="Platform admin required")

    token = impersonate_user(ctx.user, user_id, db)
    return ok({"access_token": token, "token_type": "bearer"})


@router.get("/health")
def system_health(db: Session = Depends(get_session)):
    """Public health check for monitoring dashboards — no auth required."""
    return get_system_health(db)

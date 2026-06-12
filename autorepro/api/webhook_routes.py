"""
AutoRepro Enterprise — Webhook Routes (V2.0)

CRUD for webhook registrations and delivery history.
Requires ORG_ADMIN role or above.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select
import secrets

from api.dependencies import Ctx, Page
from api.responses import ok, ok_list
from db.models_v2 import Webhook, WebhookDelivery
from db.session import get_session

router = APIRouter(prefix="/api/v1/webhooks", tags=["webhooks"])


class WebhookCreate(BaseModel):
    """Request body for registering a new webhook."""
    name: str
    url: str
    events: list[str]   # e.g. ["bug.created", "job.completed"]


@router.post("/")
def create_webhook(
    data: WebhookCreate,
    ctx: Ctx,
    db: Session = Depends(get_session),
):
    """Register a new webhook endpoint (ORG_ADMIN+ only). HMAC secret is auto-generated."""
    if not ctx.is_org_admin_or_above:
        raise HTTPException(403, detail="Org admin role or above required")

    webhook = Webhook(
        company_id=ctx.company_id,
        created_by_user_id=ctx.user_id,
        name=data.name,
        url=data.url,
        events=data.events,
        secret=secrets.token_urlsafe(32),  # Auto-generated HMAC signing key
    )
    db.add(webhook)
    db.commit()
    db.refresh(webhook)
    return ok(webhook)


@router.get("/")
def list_webhooks(ctx: Ctx, db: Session = Depends(get_session)):
    """List all webhooks for the current company (ORG_ADMIN+)."""
    if not ctx.is_org_admin_or_above:
        raise HTTPException(403, detail="Org admin role or above required")

    stmt = select(Webhook).where(
        Webhook.company_id == ctx.company_id,
        Webhook.is_deleted == False,  # noqa
    )
    webhooks = db.exec(stmt).all()
    return ok_list(webhooks, limit=len(webhooks), offset=0, total=len(webhooks))


@router.get("/{webhook_id}/deliveries")
def list_deliveries(
    webhook_id: UUID,
    ctx: Ctx,
    page: Page,
    db: Session = Depends(get_session),
):
    """View delivery history for a webhook (ORG_ADMIN+)."""
    if not ctx.is_org_admin_or_above:
        raise HTTPException(403, detail="Org admin role or above required")

    stmt = (
        select(WebhookDelivery)
        .where(WebhookDelivery.webhook_id == webhook_id)
        .order_by(WebhookDelivery.created_at.desc())
        .offset(page.offset)
        .limit(page.limit)
    )
    deliveries = db.exec(stmt).all()

    from sqlmodel import func
    total_stmt = select(func.count()).select_from(WebhookDelivery).where(
        WebhookDelivery.webhook_id == webhook_id,
    )
    total = db.exec(total_stmt).one()

    return ok_list(deliveries, limit=page.limit, offset=page.offset, total=total)


@router.patch("/{webhook_id}")
def update_webhook(
    webhook_id: UUID,
    is_active: bool,
    ctx: Ctx,
    db: Session = Depends(get_session),
):
    """Enable or disable a webhook (ORG_ADMIN+)."""
    if not ctx.is_org_admin_or_above:
        raise HTTPException(403, detail="Org admin role or above required")

    webhook = db.get(Webhook, webhook_id)
    if not webhook or webhook.company_id != ctx.company_id:
        raise HTTPException(404, detail="Webhook not found")

    webhook.is_active = is_active
    db.commit()
    return ok(webhook)

"""
AutoRepro Enterprise — Notification Routes (V2.0)

Endpoints for listing, reading, and bulk-reading user notifications.
"""

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, func, select

from api.dependencies import Ctx, Page
from api.responses import ok, ok_list
from db.models_v2 import Notification
from db.session import get_session

router = APIRouter(prefix="/api/v1/notifications", tags=["notifications"])


@router.get("/")
def list_notifications(
    ctx: Ctx,
    page: Page,
    unread_only: bool = False,
    db: Session = Depends(get_session),
):
    """
    Get the current user's notifications, newest first.

    Query params:
        unread_only: If true, only return unread notifications.

    Response metadata includes unread_count for badge rendering.
    """
    stmt = select(Notification).where(Notification.user_id == ctx.user_id)

    if unread_only:
        stmt = stmt.where(Notification.is_read == False)  # noqa

    stmt = stmt.order_by(Notification.created_at.desc())
    stmt = stmt.offset(page.offset).limit(page.limit)
    notifications = db.exec(stmt).all()

    # Count total unread for badge display
    unread_stmt = select(func.count()).select_from(Notification).where(
        Notification.user_id == ctx.user_id,
        Notification.is_read == False,  # noqa
    )
    unread_count = db.exec(unread_stmt).one()

    # Total for pagination
    total_stmt = select(func.count()).select_from(Notification).where(
        Notification.user_id == ctx.user_id,
    )
    total = db.exec(total_stmt).one()

    response = ok_list(notifications, limit=page.limit, offset=page.offset, total=total)
    response["meta"]["unread_count"] = unread_count
    return response


@router.patch("/{notification_id}/read")
def mark_read(
    notification_id: UUID,
    ctx: Ctx,
    db: Session = Depends(get_session),
):
    """Mark a single notification as read."""
    notification = db.get(Notification, notification_id)

    if not notification or notification.user_id != ctx.user_id:
        raise HTTPException(404, detail="Notification not found")

    notification.is_read = True
    notification.read_at = datetime.now(timezone.utc)
    db.commit()

    return ok(notification)


@router.post("/mark-all-read")
def mark_all_read(
    ctx: Ctx,
    db: Session = Depends(get_session),
):
    """Bulk mark all unread notifications as read for the current user."""
    stmt = select(Notification).where(
        Notification.user_id == ctx.user_id,
        Notification.is_read == False,  # noqa
    )
    notifications = db.exec(stmt).all()

    now = datetime.now(timezone.utc)
    for notification in notifications:
        notification.is_read = True
        notification.read_at = now

    db.commit()

    return ok({"message": f"Marked {len(notifications)} notifications as read"})

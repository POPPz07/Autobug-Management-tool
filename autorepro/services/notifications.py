"""
AutoRepro Enterprise — Notification Service (V2.0)

Handles in-app notifications and async email delivery.
Notifications are persisted in the DB and also broadcast via Redis pub/sub
for real-time WebSocket delivery.

Redis keys used:
  - realtime:events (PUBSUB) — global channel for WebSocket events
  - autorepro:queue:emails (LIST) — async email delivery queue
"""

import json
from typing import Optional
from uuid import UUID

import redis
from sqlmodel import Session

from db.models import Bug, User
from db.models_v2 import Notification, NotificationType
from utils.config import REDIS_URL

# Module-level Redis client
r = redis.from_url(REDIS_URL)


def create_notification(
    db: Session,
    user_id: UUID,
    type: NotificationType,
    title: str,
    message: str,
    link: Optional[str] = None,
) -> Notification:
    """
    Create an in-app notification and broadcast it via WebSocket.

    Side effects:
      - Inserts Notification record in DB.
      - Broadcasts via Redis pub/sub for real-time WebSocket delivery.
    """
    notification = Notification(
        user_id=user_id,
        type=type,
        title=title,
        message=message,
        link=link,
    )
    db.add(notification)
    db.commit()
    db.refresh(notification)

    # Broadcast via WebSocket (best-effort — notification is already persisted)
    _broadcast_notification(notification)

    return notification


def notify_bug_assigned(
    db: Session, bug: Bug, assigned_to: User, assigned_by: User
) -> None:
    """
    Helper: create a BUG_ASSIGNED notification when a bug is assigned.

    Called from services/assignment.py after assignment is committed.
    """
    create_notification(
        db,
        user_id=assigned_to.id,
        type=NotificationType.BUG_ASSIGNED,
        title=f"Bug assigned: {bug.title}",
        message=f"{assigned_by.full_name} assigned bug #{str(bug.id)[:8]} to you",
        link=f"/bugs/{bug.id}",
    )


def notify_mentions(db: Session, comment) -> None:
    """
    Parse @mentions from a comment and create BUG_MENTIONED notifications.

    Called after comment creation. Iterates comment.mentions (list of user UUIDs)
    and creates a notification for each mentioned user that exists and is not deleted.
    """
    if not comment.mentions:
        return

    for user_id in comment.mentions:
        user = db.get(User, user_id)
        if user and not user.is_deleted:
            create_notification(
                db,
                user_id=user_id,
                type=NotificationType.BUG_MENTIONED,
                title="Mentioned in comment",
                message=f"You were mentioned in bug #{str(comment.bug_id)[:8]}",
                link=f"/bugs/{comment.bug_id}#comment-{comment.id}",
            )


def send_email_async(to: str, subject: str, body: str) -> None:
    """
    Push an email job to the Redis queue for async delivery by the email worker.

    The email worker (worker/email_worker.py) polls this queue and sends via SMTP.
    """
    payload = {"to": to, "subject": subject, "body": body}
    r.rpush("autorepro:queue:emails", json.dumps(payload))


def _broadcast_notification(notification: Notification) -> None:
    """
    Internal: publish notification event to Redis pub/sub for WebSocket delivery.

    Fails silently — the notification is already persisted in DB, so the user
    will see it on their next page load even if WebSocket delivery fails.
    """
    try:
        event = {
            "type": "notification.new",
            "data": {
                "notification_id": str(notification.id),
                "title": notification.title,
                "message": notification.message,
                "link": notification.link,
                "type": notification.type,
            },
            "user_id": str(notification.user_id),
        }
        r.publish("realtime:events", json.dumps(event))
    except Exception:
        pass  # Fail silently — notification is already in DB

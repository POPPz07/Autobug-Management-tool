"""
AutoRepro Enterprise — Webhook Delivery Service (V2.0)

Finds active webhooks for a company+event, creates delivery records,
and queues them for async HTTP POST by the webhook worker.

Redis key: autorepro:queue:webhooks (LIST) — RPUSH/BLPOP
"""

import json
from uuid import UUID

import redis
from sqlmodel import Session, select

from db.models_v2 import Webhook, WebhookDelivery
from utils.config import REDIS_URL

r = redis.from_url(REDIS_URL)


def trigger_webhook(
    db: Session, company_id: UUID, event: str, payload: dict
) -> None:
    """
    Find all active webhooks for the company subscribed to this event,
    create a WebhookDelivery record for each, and push to the Redis queue.

    The webhook worker (worker/webhook_worker.py) handles the actual HTTP POST
    with HMAC signing and retry logic.

    Args:
        company_id: The company that triggered the event.
        event: Event type string (e.g. "bug.created", "job.completed").
        payload: JSON-serializable event data to send in the webhook body.
    """
    # Find active webhooks subscribed to this event
    stmt = select(Webhook).where(
        Webhook.company_id == company_id,
        Webhook.is_active == True,   # noqa: E712
        Webhook.is_deleted == False,  # noqa: E712
    )
    webhooks = db.exec(stmt).all()

    for webhook in webhooks:
        # Check if this webhook is subscribed to the event
        if webhook.events and event in webhook.events:
            # Create delivery record for audit trail
            delivery = WebhookDelivery(
                webhook_id=webhook.id,
                event=event,
                payload=payload,
            )
            db.add(delivery)
            db.commit()
            db.refresh(delivery)

            # Push to Redis queue for async delivery
            queue_payload = {
                "webhook_id": str(webhook.id),
                "delivery_id": str(delivery.id),
            }
            r.rpush("autorepro:queue:webhooks", json.dumps(queue_payload))

"""
AutoRepro Enterprise — Webhook Delivery Worker (V2.0)

Polls the Redis webhook queue and delivers payloads via HTTPS POST.
Queue key: autorepro:queue:webhooks (LIST, BLPOP)

Payload format:
    {"webhook_id": "...", "delivery_id": "..."}

Features:
  - HMAC-SHA256 payload signing (X-AutoRepro-Signature header)
  - 3 retry attempts with exponential backoff (2^attempt seconds)
  - Auto-disables webhook after max_failures consecutive failures
  - Resets failure_count to 0 on any successful delivery

Run:
    python -m worker.webhook_worker
"""

import hashlib
import hmac
import json
import logging
import time
from datetime import datetime, timezone
from uuid import UUID

import redis
import requests
from sqlmodel import Session

from db.models_v2 import Webhook, WebhookDelivery
from db.session import engine
from utils.config import REDIS_URL

logger = logging.getLogger(__name__)

r = redis.from_url(REDIS_URL)

WEBHOOK_QUEUE_KEY = "autorepro:queue:webhooks"
MAX_RETRY_ATTEMPTS = 3


def deliver_webhook(webhook: Webhook, delivery: WebhookDelivery) -> bool:
    """
    HTTP POST the webhook payload with HMAC-SHA256 signature.

    Retries up to MAX_RETRY_ATTEMPTS times with exponential backoff.

    Returns:
        True if delivery succeeded (HTTP 2xx), False otherwise.
    """
    payload_str = json.dumps(delivery.payload, default=str)

    # Generate HMAC-SHA256 signature for payload verification
    signature = hmac.new(
        webhook.secret.encode(),
        payload_str.encode(),
        hashlib.sha256,
    ).hexdigest()

    headers = {
        "Content-Type":           "application/json",
        "X-AutoRepro-Signature":  signature,
        "X-AutoRepro-Event":      delivery.event,
        "X-AutoRepro-Delivery":   str(delivery.id),
    }

    for attempt in range(1, MAX_RETRY_ATTEMPTS + 1):
        try:
            response = requests.post(
                webhook.url,
                data=payload_str,
                headers=headers,
                timeout=10,
            )

            delivery.status_code   = response.status_code
            delivery.response_body = response.text[:1000]  # Truncate long responses
            delivery.attempt       = attempt

            if response.status_code < 300:
                delivery.delivered_at = datetime.now(timezone.utc)
                logger.info(f"Webhook delivered: id={webhook.id} status={response.status_code}")
                return True
            else:
                logger.warning(f"Webhook got non-2xx: id={webhook.id} status={response.status_code} attempt={attempt}")

        except Exception as e:
            delivery.error_message = str(e)
            logger.error(f"Webhook request error: id={webhook.id} attempt={attempt} error={e}")

        # Exponential backoff between retries
        if attempt < MAX_RETRY_ATTEMPTS:
            time.sleep(2 ** attempt)

    return False


def main():
    """
    Webhook worker main loop.

    Polls queue, fetches webhook + delivery from DB, delivers, and updates records.
    """
    logger.info("Webhook worker started — polling autorepro:queue:webhooks")

    while True:
        result = r.blpop(WEBHOOK_QUEUE_KEY, timeout=5)

        if not result:
            continue

        try:
            payload = json.loads(result[1])
            webhook_id  = UUID(payload["webhook_id"])
            delivery_id = UUID(payload["delivery_id"])
        except Exception as e:
            logger.error(f"Invalid webhook queue payload: {e}")
            continue

        with Session(engine) as db:
            webhook  = db.get(Webhook, webhook_id)
            delivery = db.get(WebhookDelivery, delivery_id)

            if not webhook or not delivery:
                logger.warning(f"Webhook or delivery not found: {webhook_id}, {delivery_id}")
                continue

            success = deliver_webhook(webhook, delivery)

            if success:
                # Reset failure counter on any success
                webhook.failure_count = 0
            else:
                # Increment failure counter
                webhook.failure_count += 1
                webhook.last_failure_at = datetime.now(timezone.utc)

                # Auto-disable after max_failures consecutive failures
                if webhook.failure_count >= webhook.max_failures:
                    webhook.is_active = False
                    logger.warning(
                        f"Webhook auto-disabled after {webhook.failure_count} failures: id={webhook.id}"
                    )

            db.commit()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()

"""
AutoRepro Enterprise — Email Delivery Worker (V2.0)

Polls the Redis email queue and sends emails via SMTP.
Queue key: autorepro:queue:emails (LIST, BLPOP)

Payload format:
    {"to": "user@example.com", "subject": "...", "body": "..."}

Run:
    python -m worker.email_worker
"""

import json
import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import redis

from utils.config import (
    FROM_EMAIL,
    REDIS_URL,
    SMTP_HOST,
    SMTP_PASSWORD,
    SMTP_PORT,
    SMTP_USER,
)

logger = logging.getLogger(__name__)

# Redis client for queue polling
r = redis.from_url(REDIS_URL)

# Queue key — never hardcode these strings elsewhere
EMAIL_QUEUE_KEY = "autorepro:queue:emails"


def send_email(to: str, subject: str, body: str) -> bool:
    """
    Send a single email via SMTP.

    Uses MIMEMultipart for future HTML support.
    Returns True on success, False on failure.
    """
    try:
        msg = MIMEMultipart()
        msg["From"]    = FROM_EMAIL
        msg["To"]      = to
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))

        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
        server.ehlo()

        # Only authenticate if credentials are configured
        if SMTP_USER and SMTP_PASSWORD:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)

        server.send_message(msg)
        server.quit()

        logger.info(f"Email sent: to={to}, subject={subject[:50]}")
        return True

    except Exception as e:
        logger.error(f"Email failed: to={to}, error={e}")
        return False


def main():
    """
    Email worker main loop.

    Blocks on BLPOP with 5-second timeout to allow graceful shutdown.
    """
    logger.info("Email worker started — polling autorepro:queue:emails")

    while True:
        # BLPOP returns (key, value) tuple or None on timeout
        result = r.blpop(EMAIL_QUEUE_KEY, timeout=5)

        if result:
            try:
                payload = json.loads(result[1])
                send_email(
                    to=payload["to"],
                    subject=payload["subject"],
                    body=payload["body"],
                )
            except Exception as e:
                logger.error(f"Failed to process email payload: {e}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()

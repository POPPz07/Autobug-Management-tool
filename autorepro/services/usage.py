"""
AutoRepro Enterprise — Usage Tracking & Quota Enforcement Service (V2.0)

Enforces subscription-tier limits using Redis counters and tracks
daily usage in the CompanyUsage table for billing/analytics.

Redis keys used:
  - autorepro:user:{user_id}:rate:{YYYY-MM-DD}  — daily job count per user
  - autorepro:active_jobs:{company_id}           — concurrent job count per company
  - autorepro:gemini:rate:{company_id}:{minute}  — Gemini RPM counter (15 RPM free tier)
"""

from datetime import date, datetime, timezone
from typing import Tuple
from uuid import UUID

import redis
from sqlmodel import Session, select

from db.models import Company, Job, JobStatus
from db.models_v2 import CompanyUsage, SubscriptionPlan
from utils.config import GEMINI_RPM_LIMIT, REDIS_URL

# Module-level Redis client (decode_responses for string keys/values)
r = redis.from_url(REDIS_URL, decode_responses=True)


def check_quota(db: Session, company_id: UUID, user_id: UUID) -> Tuple[bool, str]:
    """
    Check if a user/company is allowed to run another job.

    Checks (in order):
      1. Daily job limit per user (from subscription plan)
      2. Concurrent job limit per company (from subscription plan)

    Returns:
        (True, "") if allowed.
        (False, "reason string") if blocked.
    """
    # Get company and its subscription plan
    company = db.get(Company, company_id)
    if not company or not company.subscription_plan_id:
        return (True, "")  # No plan configured = no limits enforced

    plan = db.get(SubscriptionPlan, company.subscription_plan_id)
    if not plan:
        return (True, "")

    # Check daily limit via Redis counter
    today = date.today().isoformat()
    user_daily_key = f"autorepro:user:{user_id}:rate:{today}"
    user_today_count = int(r.get(user_daily_key) or 0)

    if user_today_count >= plan.max_jobs_per_day:
        return (False, f"Daily limit reached ({plan.max_jobs_per_day} jobs/day)")

    # Check concurrent limit via Redis counter
    active_jobs_key = f"autorepro:active_jobs:{company_id}"
    active_count = int(r.get(active_jobs_key) or 0)

    if active_count >= plan.max_concurrent_jobs:
        return (False, f"Concurrent job limit reached ({plan.max_concurrent_jobs})")

    return (True, "")


def check_gemini_rate_limit(company_id: UUID) -> bool:
    """
    Enforce Gemini free-tier rate limit (15 RPM by default).

    Uses a Redis counter keyed by company + current minute.
    The key auto-expires after 60 seconds.

    Returns:
        True if the request is allowed, False if rate-limited.
    """
    minute = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H-%M")
    key = f"autorepro:gemini:rate:{company_id}:{minute}"

    count = r.incr(key)
    r.expire(key, 60)  # TTL 60 seconds — auto-cleanup

    return count <= GEMINI_RPM_LIMIT


def record_job_usage(db: Session, company_id: UUID, job: Job) -> None:
    """
    Increment daily CompanyUsage counters after job completion.

    Called by the worker in the `finally` block after every job.
    Creates the CompanyUsage row for today if it doesn't exist yet.
    """
    today = date.today()

    # Get or create today's usage record
    stmt = select(CompanyUsage).where(
        CompanyUsage.company_id == company_id,
        CompanyUsage.date == today,
    )
    usage = db.exec(stmt).first()

    if not usage:
        usage = CompanyUsage(company_id=company_id, date=today)
        db.add(usage)

    # Increment job counters
    usage.jobs_run += 1

    if job.status == JobStatus.SUCCESS:
        usage.jobs_succeeded += 1
    elif job.status == JobStatus.FAILED:
        if job.cancelled_at:
            usage.jobs_cancelled += 1
        else:
            usage.jobs_failed += 1

    # Track token usage by provider
    if job.llm_used == "gemini":
        usage.gemini_tokens_input += job.gemini_tokens_input
        usage.gemini_tokens_output += job.gemini_tokens_output
    elif job.llm_used == "groq":
        usage.groq_tokens_input += job.gemini_tokens_input
        usage.groq_tokens_output += job.gemini_tokens_output

    db.commit()


def get_usage_summary(
    db: Session, company_id: UUID, start_date: date, end_date: date
) -> dict:
    """
    Aggregate usage metrics for a date range (used in billing/analytics dashboards).

    Returns:
        {
            "total_jobs": int,
            "successful": int,
            "failed": int,
            "cancelled": int,
            "gemini_tokens": int,
            "groq_tokens": int,
            "storage_mb": float,
        }
    """
    stmt = select(CompanyUsage).where(
        CompanyUsage.company_id == company_id,
        CompanyUsage.date >= start_date,
        CompanyUsage.date <= end_date,
    )
    records = db.exec(stmt).all()

    return {
        "total_jobs": sum(r.jobs_run for r in records),
        "successful": sum(r.jobs_succeeded for r in records),
        "failed": sum(r.jobs_failed for r in records),
        "cancelled": sum(r.jobs_cancelled for r in records),
        "gemini_tokens": sum(r.gemini_tokens_input + r.gemini_tokens_output for r in records),
        "groq_tokens": sum(r.groq_tokens_input + r.groq_tokens_output for r in records),
        "storage_mb": sum(r.storage_used_mb for r in records),
    }

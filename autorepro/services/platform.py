"""
AutoRepro Enterprise — Platform Admin Service (V2.0)

Provides company management, system health checks, and user impersonation
for PLATFORM_ADMIN users. All operations are logged to ActivityLog.

Design:
  - create_company() validates plan existence and slug uniqueness.
  - get_system_health() probes Postgres and Redis for monitoring dashboards.
  - impersonate_user() generates a JWT for a target user (audit-logged).
"""

from uuid import UUID

import redis
from sqlmodel import Session, select

from db.models import ActivityLog, Company, User
from db.models_v2 import SubscriptionPlan
from utils.config import REDIS_URL


def create_company(db: Session, name: str, slug: str, plan_id: UUID) -> Company:
    """
    Create a new tenant company with the given subscription plan.

    Validations:
      - Subscription plan must exist.
      - Slug must be unique across all companies.

    Returns:
        The newly created Company record.

    Raises:
        ValueError: If plan not found or slug already taken.
    """
    # Validate plan exists
    plan = db.get(SubscriptionPlan, plan_id)
    if not plan:
        raise ValueError("Subscription plan not found")

    # Check slug uniqueness
    stmt = select(Company).where(Company.slug == slug)
    if db.exec(stmt).first():
        raise ValueError("Slug already taken")

    company = Company(
        name=name,
        slug=slug,
        subscription_plan_id=plan_id,
    )
    db.add(company)
    db.commit()
    db.refresh(company)

    return company


def get_system_health(db: Session) -> dict:
    """
    System health check for monitoring dashboards.

    Probes:
      - Postgres: SELECT 1
      - Redis: PING + queue depth

    Returns:
        {
            "status": "healthy" | "degraded",
            "postgres": {"status": "ok"} | {"status": "error", "message": "..."},
            "redis": {"status": "ok", "queue_depth": N} | {"status": "error", ...},
        }
    """
    health = {"status": "healthy"}

    # Postgres probe
    try:
        db.exec(select(User).limit(1))
        health["postgres"] = {"status": "ok"}
    except Exception as e:
        health["postgres"] = {"status": "error", "message": str(e)}
        health["status"] = "degraded"

    # Redis probe
    try:
        r = redis.from_url(REDIS_URL)
        r.ping()
        queue_depth = r.llen("autorepro:queue:jobs:normal")
        health["redis"] = {"status": "ok", "queue_depth": queue_depth}
    except Exception as e:
        health["redis"] = {"status": "error", "message": str(e)}
        health["status"] = "degraded"

    return health


def impersonate_user(admin_user: User, target_user_id: UUID, db: Session) -> str:
    """
    Generate a JWT for the target user (PLATFORM_ADMIN only).

    This allows platform admins to debug issues as a specific user.
    The impersonation is logged to ActivityLog for audit compliance.

    Returns:
        JWT access token string for the target user.

    Raises:
        ValueError: If the target user doesn't exist.
    """
    target_user = db.get(User, target_user_id)
    if not target_user:
        raise ValueError("User not found")

    # Audit log — impersonation is a sensitive action
    log = ActivityLog(
        entity_type="USER",
        entity_id=target_user_id,
        action="IMPERSONATED",
        user_id=admin_user.id,
        company_id=admin_user.company_id,
        metadata_json={"target_user_id": str(target_user_id)},
    )
    db.add(log)
    db.commit()

    # Generate JWT (lazy import to avoid circular dependency)
    from api.auth import create_access_token
    token = create_access_token(target_user)
    return token

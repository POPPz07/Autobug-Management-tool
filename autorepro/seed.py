"""
AutoRepro Enterprise — Seed Data Script (V2.0)

Creates the foundational data required for the system to operate:
  1. Default subscription plans (FREE, STARTER, PRO, ENTERPRISE)
  2. A platform admin user
  3. A test company on the FREE plan

Usage:
    python -m autorepro.seed
    # or from the autorepro directory:
    python seed.py
"""

import uuid
from datetime import datetime, timezone

from sqlmodel import Session, select

from db.models import Company, User, UserRole
from db.models_v2 import SubscriptionPlan
from db.session import engine


def seed_subscription_plans(db: Session) -> dict[str, SubscriptionPlan]:
    """
    Insert the four default subscription plans if they don't already exist.

    Returns:
        Dict mapping plan name to SubscriptionPlan object.
    """
    plans_data = [
        {
            "name": "FREE",
            "display_name": "Free Tier",
            "max_jobs_per_day": 10,
            "max_concurrent_jobs": 2,
            "max_bugs": 50,
            "max_team_members": 5,
            "max_teams": 2,
            "max_storage_mb": 100,
            "features": {"webhooks": False, "api_access": True, "priority_support": False},
            "price_monthly_cents": 0,
            "price_yearly_cents": 0,
        },
        {
            "name": "STARTER",
            "display_name": "Starter Plan",
            "max_jobs_per_day": 50,
            "max_concurrent_jobs": 5,
            "max_bugs": 200,
            "max_team_members": 20,
            "max_teams": 5,
            "max_storage_mb": 500,
            "features": {"webhooks": True, "api_access": True, "priority_support": False},
            "price_monthly_cents": 2900,
            "price_yearly_cents": 29000,
        },
        {
            "name": "PRO",
            "display_name": "Professional",
            "max_jobs_per_day": 200,
            "max_concurrent_jobs": 10,
            "max_bugs": None,  # unlimited
            "max_team_members": 100,
            "max_teams": None,
            "max_storage_mb": 5000,
            "features": {"webhooks": True, "api_access": True, "priority_support": True, "advanced_analytics": True},
            "price_monthly_cents": 9900,
            "price_yearly_cents": 99000,
        },
        {
            "name": "ENTERPRISE",
            "display_name": "Enterprise",
            "max_jobs_per_day": 999999,  # effectively unlimited
            "max_concurrent_jobs": 50,
            "max_bugs": None,
            "max_team_members": None,
            "max_teams": None,
            "max_storage_mb": None,
            "features": {
                "webhooks": True, "api_access": True, "priority_support": True,
                "advanced_analytics": True, "custom_retention": True,
            },
            "price_monthly_cents": 0,  # custom pricing
            "price_yearly_cents": 0,
        },
    ]

    created_plans = {}
    for plan_data in plans_data:
        # Check if plan already exists
        stmt = select(SubscriptionPlan).where(SubscriptionPlan.name == plan_data["name"])
        existing = db.exec(stmt).first()

        if existing:
            print(f"  [SKIP] Plan '{plan_data['name']}' already exists")
            created_plans[plan_data["name"]] = existing
        else:
            plan = SubscriptionPlan(**plan_data)
            db.add(plan)
            print(f"  [CREATE] Plan '{plan_data['name']}' — {plan_data['display_name']}")
            created_plans[plan_data["name"]] = plan

    db.commit()

    # Refresh all plans to get IDs
    for name, plan in created_plans.items():
        db.refresh(plan)

    return created_plans


def seed_platform_admin(db: Session, company_id: uuid.UUID) -> User:
    """
    Create the default PLATFORM_ADMIN user if it doesn't exist.
    """
    email = "admin@autorepro.dev"
    stmt = select(User).where(User.email == email)
    existing = db.exec(stmt).first()

    if existing:
        print(f"  [SKIP] Platform admin '{email}' already exists")
        return existing

    # Hash password (uses pwdlib argon2)
    from pwdlib import PasswordHash
    ph = PasswordHash.recommended()

    admin = User(
        full_name="Platform Admin",
        email=email,
        password_hash=ph.hash("admin123"),  # Change in production!
        role=UserRole.PLATFORM_ADMIN,
        is_active=True,
        company_id=company_id,
        email_verified=True,
    )
    db.add(admin)
    db.commit()
    db.refresh(admin)

    print(f"  [CREATE] Platform admin '{email}' (password: admin123)")
    return admin


def seed_test_company(db: Session, free_plan_id: uuid.UUID) -> Company:
    """
    Create a test company on the FREE plan if it doesn't exist.
    """
    slug = "autorepro-test"
    stmt = select(Company).where(Company.slug == slug)
    existing = db.exec(stmt).first()

    if existing:
        print(f"  [SKIP] Test company '{slug}' already exists")
        return existing

    company = Company(
        name="AutoRepro Test Company",
        slug=slug,
        subscription_plan_id=free_plan_id,
        settings={"enable_webhooks": False, "max_attachment_size_mb": 10},
    )
    db.add(company)
    db.commit()
    db.refresh(company)

    print(f"  [CREATE] Test company '{company.name}' (slug: {slug})")
    return company


def main():
    """Run all seed operations in order."""
    print("=" * 60)
    print("AutoRepro Enterprise — Seed Data Script")
    print("=" * 60)

    with Session(engine) as db:
        print("\n1. Seeding subscription plans...")
        plans = seed_subscription_plans(db)

        print("\n2. Seeding test company...")
        company = seed_test_company(db, plans["FREE"].id)

        print("\n3. Seeding platform admin...")
        admin = seed_platform_admin(db, company.id)

        print("\n" + "=" * 60)
        print("Seed complete!")
        print(f"  Plans:   {', '.join(plans.keys())}")
        print(f"  Company: {company.name} (id: {company.id})")
        print(f"  Admin:   {admin.email}")
        print("=" * 60)


if __name__ == "__main__":
    main()

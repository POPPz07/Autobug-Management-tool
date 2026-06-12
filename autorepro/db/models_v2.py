"""
AutoRepro Enterprise — V2.0 New Database Models

Contains all 13 new models added in the V2.0 SaaS refactor:
  9.  SubscriptionPlan      — tier limits (FREE/STARTER/PRO/ENTERPRISE)
  10. CompanyUsage           — daily usage rollup per company
  11. BugTemplate            — reusable bug description templates
  12. BugLabel               — colored labels for bug categorization
  13. BugLabelLink           — many-to-many join (bug <-> label)
  14. BugDependency          — bug-blocks-bug relationships
  15. BugAttachment          — local file uploads on bugs
  16. Notification           — in-app notification records
  17. ApiKey                 — programmatic API access keys
  18. Webhook                — outbound webhook registrations
  19. WebhookDelivery        — webhook delivery attempt log
  20. PasswordResetToken     — time-limited reset tokens
  21. WebSocketConnection    — active WS connection registry

Also defines two new enums: NotificationType, WebhookEvent.
"""

import uuid
from datetime import datetime, date as dt_date, timezone
from enum import Enum
from typing import Optional

from sqlmodel import Field, SQLModel, CheckConstraint
from sqlalchemy import Column, JSON, Text

from db.models import BaseTenantModel, BugPriority, BugSeverity


# ═══════════════════════════════════════════════════════════════════
# NEW ENUMS
# ═══════════════════════════════════════════════════════════════════

class NotificationType(str, Enum):
    """Types of in-app notifications delivered to users."""
    BUG_ASSIGNED      = "bug_assigned"
    BUG_MENTIONED     = "bug_mentioned"
    BUG_COMMENT_REPLY = "bug_comment_reply"
    JOB_COMPLETED     = "job_completed"
    JOB_FAILED        = "job_failed"
    QUOTA_WARNING     = "quota_warning"
    QUOTA_EXCEEDED    = "quota_exceeded"


class WebhookEvent(str, Enum):
    """Events that can trigger outbound webhook deliveries."""
    BUG_CREATED        = "bug.created"
    BUG_UPDATED        = "bug.updated"
    BUG_ASSIGNED       = "bug.assigned"
    BUG_STATUS_CHANGED = "bug.status_changed"
    JOB_STARTED        = "job.started"
    JOB_COMPLETED      = "job.completed"
    JOB_FAILED         = "job.failed"


# ═══════════════════════════════════════════════════════════════════
# 9. TABLE: subscription_plans
# Defines quota limits and feature flags per pricing tier.
# Seed data: FREE, STARTER, PRO, ENTERPRISE (inserted via seed.py).
# ═══════════════════════════════════════════════════════════════════

class SubscriptionPlan(SQLModel, table=True):
    __tablename__ = "subscription_plans"

    id:           uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    name:         str       = Field(max_length=50, unique=True, description="FREE, STARTER, PRO, ENTERPRISE")
    display_name: str       = Field(max_length=100, description='Human-readable: "Free Tier"')

    # Quota limits (null = unlimited)
    max_jobs_per_day:    int            = Field(default=10)
    max_concurrent_jobs: int            = Field(default=2)
    max_bugs:            Optional[int]  = Field(default=None, description="null = unlimited")
    max_team_members:    Optional[int]  = Field(default=None)
    max_teams:           Optional[int]  = Field(default=None)
    max_storage_mb:      Optional[int]  = Field(default=None)

    # Feature flags as JSON: {"webhooks": true, "api_access": true, ...}
    features: Optional[dict] = Field(default=None, sa_column=Column(JSON))

    # Pricing (for future billing integration)
    price_monthly_cents: int = Field(default=0, description="$0.00 = 0, $49.00 = 4900")
    price_yearly_cents:  int = Field(default=0)

    is_active:  bool     = Field(default=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ═══════════════════════════════════════════════════════════════════
# 10. TABLE: company_usage
# Daily rollup of job/token/storage metrics per company.
# One row per company per day. Used for billing and quota enforcement.
# ═══════════════════════════════════════════════════════════════════

class CompanyUsage(SQLModel, table=True):
    __tablename__ = "company_usage"

    id:         uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    company_id: uuid.UUID = Field(foreign_key="companies.id", index=True)
    date:       dt_date      = Field(index=True, description="Daily rollup date")

    # Job metrics
    jobs_run:       int = Field(default=0)
    jobs_succeeded: int = Field(default=0)
    jobs_failed:    int = Field(default=0)
    jobs_cancelled: int = Field(default=0)

    # Token usage by provider
    gemini_tokens_input:  int = Field(default=0)
    gemini_tokens_output: int = Field(default=0)
    groq_tokens_input:    int = Field(default=0)
    groq_tokens_output:   int = Field(default=0)

    # Storage
    attachments_uploaded: int   = Field(default=0)
    storage_used_mb:      float = Field(default=0.0)

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ═══════════════════════════════════════════════════════════════════
# 11. TABLE: bug_templates
# Reusable bug description templates with {{placeholder}} support.
# ═══════════════════════════════════════════════════════════════════

class BugTemplate(BaseTenantModel, table=True):
    __tablename__ = "bug_templates"

    id:         uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    company_id: uuid.UUID = Field(foreign_key="companies.id", index=True)
    name:       str       = Field(max_length=255)

    # Template body with {{placeholders}}: "Login failed on {{browser}} using {{auth_type}}"
    description_template: str = Field(sa_column=Column(Text))

    default_priority:    BugPriority = Field(default=BugPriority.MEDIUM)
    default_severity:    BugSeverity = Field(default=BugSeverity.MEDIUM)
    default_environment: str         = Field(default="production", max_length=50)

    created_by_user_id: uuid.UUID = Field(foreign_key="users.id")


# ═══════════════════════════════════════════════════════════════════
# 12. TABLE: bug_labels
# Company-scoped colored labels for bug categorization.
# ═══════════════════════════════════════════════════════════════════

class BugLabel(BaseTenantModel, table=True):
    __tablename__ = "bug_labels"

    id:         uuid.UUID    = Field(default_factory=uuid.uuid4, primary_key=True)
    company_id: uuid.UUID    = Field(foreign_key="companies.id", index=True)
    name:       str          = Field(max_length=50)
    color:      str          = Field(max_length=7, description='Hex color e.g. "#FF5733"')
    description: Optional[str] = Field(default=None, max_length=255)


# ═══════════════════════════════════════════════════════════════════
# 13. TABLE: bug_label_links (many-to-many join)
# ═══════════════════════════════════════════════════════════════════

class BugLabelLink(SQLModel, table=True):
    __tablename__ = "bug_label_links"

    bug_id:     uuid.UUID = Field(foreign_key="bugs.id", primary_key=True)
    label_id:   uuid.UUID = Field(foreign_key="bug_labels.id", primary_key=True)
    created_at: datetime  = Field(default_factory=lambda: datetime.now(timezone.utc))


# ═══════════════════════════════════════════════════════════════════
# 14. TABLE: bug_dependencies
# Tracks "bug A blocks bug B" relationships.
# CheckConstraint prevents self-referencing dependencies.
# ═══════════════════════════════════════════════════════════════════

class BugDependency(SQLModel, table=True):
    __tablename__ = "bug_dependencies"

    id:                 uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    bug_id:             uuid.UUID = Field(foreign_key="bugs.id", index=True, description="This bug")
    blocks_bug_id:      uuid.UUID = Field(foreign_key="bugs.id", index=True, description="Is blocked by this bug")
    created_at:         datetime  = Field(default_factory=lambda: datetime.now(timezone.utc))
    created_by_user_id: uuid.UUID = Field(foreign_key="users.id")


# ═══════════════════════════════════════════════════════════════════
# 15. TABLE: bug_attachments
# Local filesystem file uploads attached to bugs.
# Path: /data/attachments/{company_id}/{bug_id}/{uuid}_{filename}
# ═══════════════════════════════════════════════════════════════════

class BugAttachment(BaseTenantModel, table=True):
    __tablename__ = "bug_attachments"

    id:                  uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    bug_id:              uuid.UUID = Field(foreign_key="bugs.id", index=True)
    company_id:          uuid.UUID = Field(foreign_key="companies.id", index=True)
    uploaded_by_user_id: uuid.UUID = Field(foreign_key="users.id")

    filename:   str = Field(max_length=255, description="Original filename")
    filepath:   str = Field(max_length=512, description="Local path on server filesystem")
    size_bytes: int = Field(description="File size in bytes")
    mime_type:  str = Field(max_length=100, description="e.g. image/png, video/mp4")


# ═══════════════════════════════════════════════════════════════════
# 16. TABLE: notifications
# In-app notification records. Delivered via WebSocket/SSE in real-time.
# ═══════════════════════════════════════════════════════════════════

class Notification(SQLModel, table=True):
    __tablename__ = "notifications"

    id:      uuid.UUID        = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID        = Field(foreign_key="users.id", index=True)
    type:    NotificationType = Field(description="Notification category")
    title:   str              = Field(max_length=255)
    message: str              = Field(sa_column=Column(Text))
    link:    Optional[str]    = Field(default=None, max_length=512, description='e.g. "/bugs/{id}"')

    is_read: bool                = Field(default=False)
    read_at: Optional[datetime]  = Field(default=None)

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), index=True)


# ═══════════════════════════════════════════════════════════════════
# 17. TABLE: api_keys
# Programmatic API access keys for CI/CD integrations.
# Only the bcrypt hash is stored; the raw key is shown once at creation.
# ═══════════════════════════════════════════════════════════════════

class ApiKey(SQLModel, table=True):
    __tablename__ = "api_keys"

    id:                 uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    company_id:         uuid.UUID = Field(foreign_key="companies.id", index=True)
    created_by_user_id: uuid.UUID = Field(foreign_key="users.id")

    name:     str = Field(max_length=100, description='"CI/CD Pipeline", "Jira Integration"')
    key_hash: str = Field(max_length=128, unique=True, index=True, description="bcrypt hash of raw key")
    prefix:   str = Field(max_length=12, description='"ak_live_abc123" — shown in UI for identification')

    last_used_at: Optional[datetime] = Field(default=None)
    is_active:    bool               = Field(default=True)

    created_at: datetime           = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: Optional[datetime] = Field(default=None, description="Optional expiration date")


# ═══════════════════════════════════════════════════════════════════
# 18. TABLE: webhooks
# Outbound webhook registrations. HMAC-signed payloads.
# Auto-disabled after max_failures consecutive delivery failures.
# ═══════════════════════════════════════════════════════════════════

class Webhook(BaseTenantModel, table=True):
    __tablename__ = "webhooks"

    id:                 uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    company_id:         uuid.UUID = Field(foreign_key="companies.id", index=True)
    created_by_user_id: uuid.UUID = Field(foreign_key="users.id")

    name:   str           = Field(max_length=100, description='"Slack Notifications"')
    url:    str           = Field(max_length=512)
    events: Optional[list] = Field(sa_column=Column(JSON), description='["bug.created", "job.completed"]')
    secret: str           = Field(max_length=128, description="HMAC-SHA256 signing key")

    is_active:       bool               = Field(default=True)
    failure_count:   int                = Field(default=0, description="Consecutive failed deliveries")
    last_failure_at: Optional[datetime] = Field(default=None)
    max_failures:    int                = Field(default=10, description="Auto-disable after this many consecutive failures")


# ═══════════════════════════════════════════════════════════════════
# 19. TABLE: webhook_deliveries
# Log of every webhook delivery attempt (success or failure).
# ═══════════════════════════════════════════════════════════════════

class WebhookDelivery(SQLModel, table=True):
    __tablename__ = "webhook_deliveries"

    id:         uuid.UUID    = Field(default_factory=uuid.uuid4, primary_key=True)
    webhook_id: uuid.UUID    = Field(foreign_key="webhooks.id", index=True)
    event:      str          = Field(max_length=50, description='"bug.created"')
    payload:    Optional[dict] = Field(sa_column=Column(JSON))

    # Delivery results
    status_code:   Optional[int] = Field(default=None)
    response_body: Optional[str] = Field(default=None, sa_column=Column(Text))
    error_message: Optional[str] = Field(default=None, sa_column=Column(Text))

    attempt:      int               = Field(default=1, description="Retry counter")
    delivered_at: Optional[datetime] = Field(default=None)

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), index=True)


# ═══════════════════════════════════════════════════════════════════
# 20. TABLE: password_reset_tokens
# Time-limited password reset tokens. SHA256 hash stored, raw sent via email.
# ═══════════════════════════════════════════════════════════════════

class PasswordResetToken(SQLModel, table=True):
    __tablename__ = "password_reset_tokens"

    id:         uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id:    uuid.UUID = Field(foreign_key="users.id", index=True)
    token_hash: str       = Field(max_length=128, unique=True, index=True, description="SHA256 hash")

    expires_at: datetime           = Field(description="Token expiration (1 hour from creation)")
    used_at:    Optional[datetime] = Field(default=None)

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ═══════════════════════════════════════════════════════════════════
# 21. TABLE: websocket_connections
# Registry of active WebSocket connections for health monitoring.
# ═══════════════════════════════════════════════════════════════════

class WebSocketConnection(SQLModel, table=True):
    __tablename__ = "websocket_connections"

    id:            uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id:       uuid.UUID = Field(foreign_key="users.id", index=True)
    connection_id: str       = Field(max_length=128, unique=True, description="Unique socket ID")

    connected_at:    datetime           = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_ping_at:    datetime           = Field(default_factory=lambda: datetime.now(timezone.utc))
    disconnected_at: Optional[datetime] = Field(default=None)

    is_active: bool = Field(default=True)

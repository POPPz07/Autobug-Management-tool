"""
AutoRepro Enterprise — Database Models (Phase 1 Final)

Design decisions:
  - BaseTenantModel enforces company isolation + soft delete on all entity tables.
  - SQLAlchemy 2.x relationship mappers are intentionally omitted to avoid
    forward-ref mapper conflicts; all joins use explicit UUID FK columns.
  - Explicit index=True on all high-cardinality query paths.
  - No Celery dependency anywhere in this file.
"""

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from sqlmodel import Field, SQLModel
from sqlalchemy import Column, Text
import sqlalchemy.dialects.postgresql as pg


# ═══════════════════════════════════════════════════════════════════
# ENUMS
# ═══════════════════════════════════════════════════════════════════

class UserRole(str, Enum):
    """Six-tier RBAC hierarchy (lowest -> highest privilege) + internal SYSTEM role."""
    DEVELOPER      = "DEVELOPER"       # 1 - fixes bugs
    TESTER         = "TESTER"          # 2 - runs AutoRepro
    SUPERVISOR     = "SUPERVISOR"      # 3 - manages a team
    MANAGER        = "MANAGER"         # 4 - oversees multiple teams
    ORG_ADMIN      = "ORG_ADMIN"       # 5 - company owner
    PLATFORM_ADMIN = "PLATFORM_ADMIN"  # 6 - full system control
    SYSTEM         = "SYSTEM"          # 7 - internal worker role (not assignable to humans)


class BugStatus(str, Enum):
    """
    Bug lifecycle states — strict linear progression.
    RUNNING_AUTOREPRO is set by the worker when execution starts;
    routes must not allow direct transition into it (only via job trigger).
    """
    CREATED            = "CREATED"
    TRIAGED            = "TRIAGED"
    ASSIGNED           = "ASSIGNED"
    IN_PROGRESS        = "IN_PROGRESS"
    RUNNING_AUTOREPRO  = "RUNNING_AUTOREPRO"   # set by worker, not directly by users
    RESOLVED           = "RESOLVED"
    CLOSED             = "CLOSED"
    DUPLICATE          = "DUPLICATE"


class JobStatus(str, Enum):
    """
    AutoRepro execution status only — represents what the worker is doing.
    NEVER used to represent bug workflow state.

    PENDING → RUNNING → SUCCESS | FAILED
    """
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED  = "FAILED"


class BugPriority(str, Enum):
    LOW      = "LOW"
    MEDIUM   = "MEDIUM"
    HIGH     = "HIGH"
    CRITICAL = "CRITICAL"


class BugSeverity(str, Enum):
    LOW      = "LOW"
    MEDIUM   = "MEDIUM"
    HIGH     = "HIGH"
    CRITICAL = "CRITICAL"


class ConfidenceLevel(str, Enum):
    LOW    = "LOW"
    MEDIUM = "MEDIUM"
    HIGH   = "HIGH"


# ═══════════════════════════════════════════════════════════════════
# BASE MODEL
# ═══════════════════════════════════════════════════════════════════

class BaseTenantModel(SQLModel):
    """
    Inherited by all tenant-scoped tables.
    Provides:
      - is_deleted  → soft delete; ALL list queries must filter is_deleted=False
      - created_at  → immutable creation timestamp
      - updated_at  → mutable; must be set manually on every UPDATE
    """
    is_deleted: bool     = Field(default=False, index=True)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        index=True,
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )


# ═══════════════════════════════════════════════════════════════════
# TABLE: companies
# ═══════════════════════════════════════════════════════════════════

class Company(BaseTenantModel, table=True):
    __tablename__ = "companies"

    id:   uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    name: str       = Field(index=True, max_length=255)
    slug: str       = Field(unique=True, index=True, max_length=100)  # URL-safe identifier


# ═══════════════════════════════════════════════════════════════════
# TABLE: users
# ═══════════════════════════════════════════════════════════════════

class User(BaseTenantModel, table=True):
    __tablename__ = "users"

    id:            uuid.UUID       = Field(default_factory=uuid.uuid4, primary_key=True)
    full_name:     str             = Field(index=True, max_length=255)
    email:         str             = Field(unique=True, index=True, max_length=255)
    password_hash: str             = Field(sa_column=Column(Text))
    role:          UserRole        = Field(default=UserRole.TESTER, index=True)
    is_active:     bool            = Field(default=True)

    # FK constraints
    company_id: uuid.UUID           = Field(foreign_key="companies.id", index=True)
    team_id:    Optional[uuid.UUID] = Field(default=None, foreign_key="teams.id", index=True)


# ═══════════════════════════════════════════════════════════════════
# TABLE: teams
# ═══════════════════════════════════════════════════════════════════

class Team(BaseTenantModel, table=True):
    __tablename__ = "teams"

    id:          uuid.UUID       = Field(default_factory=uuid.uuid4, primary_key=True)
    name:        str             = Field(index=True, max_length=255)
    description: Optional[str]  = Field(default=None, sa_column=Column(Text))

    # FK constraints
    company_id:    uuid.UUID           = Field(foreign_key="companies.id", index=True)
    supervisor_id: Optional[uuid.UUID] = Field(
        default=None,
        foreign_key="users.id",
        index=True,
        description="The Supervisor who owns this team",
    )


# ═══════════════════════════════════════════════════════════════════
# TABLE: bugs
# ═══════════════════════════════════════════════════════════════════

class Bug(BaseTenantModel, table=True):
    __tablename__ = "bugs"

    id:          uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    title:       str       = Field(index=True, max_length=500)
    description: str       = Field(sa_column=Column(Text))
    target_url:  str       = Field(max_length=2048)

    # ── Lifecycle (BugStatus) ──────────────────────────────────────
    status: BugStatus = Field(default=BugStatus.CREATED, index=True)

    # ── Classification ────────────────────────────────────────────
    priority: BugPriority = Field(default=BugPriority.MEDIUM, index=True)
    severity: BugSeverity = Field(default=BugSeverity.MEDIUM, index=True)

    # ── Environment metadata (explicit fields, NOT an array) ───────
    environment: str            = Field(default="production", max_length=50)   # dev / staging / production
    browser:     Optional[str] = Field(default=None, max_length=100)
    os:          Optional[str] = Field(default=None, max_length=100)
    device:      Optional[str] = Field(default=None, max_length=100)

    # ── AI execution output layer ──────────────────────────────────
    # reproducibility_score: float 0.0–1.0 (success_count / total_runs).
    # Stored as a fraction, NOT a percentage. Multiply by 100 for display only.
    reproducibility_score: Optional[float]           = Field(default=None, ge=0.0, le=1.0)
    confidence_level:      Optional[ConfidenceLevel]  = Field(default=None)
    latest_job_id:         Optional[uuid.UUID]        = Field(default=None, foreign_key="jobs.id")


    # ── Deduplication ─────────────────────────────────────────────
    duplicate_of: Optional[uuid.UUID] = Field(default=None, foreign_key="bugs.id")

    # ── Ownership (FK constraints) ─────────────────────────────────
    # created_by_user_id: who originally filed the bug (immutable)
    # current_assignee_id: denormalized fast-lookup (mirrors latest BugAssignment)
    created_by_user_id:  uuid.UUID           = Field(foreign_key="users.id", index=True)
    current_assignee_id: Optional[uuid.UUID] = Field(default=None, foreign_key="users.id", index=True)
    reported_by:         uuid.UUID           = Field(foreign_key="users.id", index=True)
    assigned_to:         Optional[uuid.UUID] = Field(default=None, foreign_key="users.id", index=True)
    team_id:             Optional[uuid.UUID] = Field(default=None, foreign_key="teams.id", index=True)
    company_id:          uuid.UUID           = Field(foreign_key="companies.id", index=True)

    # ── Execution statistics ────────────────────────────────────────
    # failure_count: incremented by worker on each failed job (never decremented)
    failure_count: int = Field(default=0, description="Cumulative AutoRepro execution failures")



# ═══════════════════════════════════════════════════════════════════
# TABLE: bug_assignments
# Assignment history (who assigned to whom, and when).
# ═══════════════════════════════════════════════════════════════════

class BugAssignment(SQLModel, table=True):
    __tablename__ = "bug_assignments"

    id:         uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    created_at: datetime  = Field(default_factory=lambda: datetime.now(timezone.utc), index=True)

    # FK constraints — all three are mandatory
    bug_id:             uuid.UUID           = Field(foreign_key="bugs.id", index=True)
    assigned_to_user_id: uuid.UUID          = Field(foreign_key="users.id", index=True)
    assigned_by_user_id: uuid.UUID          = Field(foreign_key="users.id")
    team_id:             Optional[uuid.UUID] = Field(default=None, foreign_key="teams.id", index=True)

    note: Optional[str] = Field(default=None, sa_column=Column(Text))


# ═══════════════════════════════════════════════════════════════════
# TABLE: jobs
# Each row = one AutoRepro execution attempt for a bug.
# JobStatus tracks execution state ONLY — never used for bug lifecycle.
# ═══════════════════════════════════════════════════════════════════

class Job(SQLModel, table=True):
    """
    One row = one AutoRepro execution attempt.

    Fields:
      - attempt_number: 1-indexed; incremented by trigger service each re-run.
      - max_attempts:   ceiling copied from config at creation time (worker enforces).
      - llm_used:       set ONCE at job creation by the trigger service; never overwritten.
      - job_hash:       sha256(bug.description + bug.target_url); guards against
                        duplicate identical submissions when last SUCCESS < 24h ago.
    """
    __tablename__ = "jobs"

    id:           uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    created_at:   datetime  = Field(default_factory=lambda: datetime.now(timezone.utc), index=True)
    completed_at: Optional[datetime] = Field(default=None)

    # FK constraint
    bug_id: uuid.UUID = Field(foreign_key="bugs.id", index=True)

    # Execution tracking
    status:           JobStatus      = Field(default=JobStatus.PENDING, index=True)
    attempt_number:   int            = Field(default=1)   # 1-indexed
    max_attempts:     int            = Field(default=5)   # ceiling from config at creation
    llm_used:         Optional[str]  = Field(default=None, max_length=100)  # set once on creation
    duration_seconds: Optional[float] = Field(default=None)

    # Deduplication — sha256(description + target_url), computed at trigger time
    job_hash: Optional[str] = Field(default=None, max_length=64, index=True)

    # Outputs
    script:                 Optional[str]  = Field(default=None, sa_column=Column(Text))
    logs:                   Optional[str]  = Field(default=None, sa_column=Column(Text))
    failure_reason_summary: Optional[str]  = Field(default=None, sa_column=Column(Text))

    # Screenshots: JSON array of relative artifact paths ["artifacts/{job_id}/s1.png", ...]
    screenshots: Optional[list] = Field(default=None, sa_column=Column(pg.JSONB))

    # Structured execution steps — populated by the worker after agent completion.
    # Schema: [{"step": int, "label": str, "status": "ok"|"fail", "detail": str|None}]
    # Stored as JSONB; None until the job completes.
    steps: Optional[list] = Field(default=None, sa_column=Column(pg.JSONB))




# ═══════════════════════════════════════════════════════════════════
# TABLE: comments
# ═══════════════════════════════════════════════════════════════════

class Comment(BaseTenantModel, table=True):
    __tablename__ = "comments"

    id:      uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    message: str       = Field(sa_column=Column(Text))

    # FK constraints
    bug_id:  uuid.UUID = Field(foreign_key="bugs.id", index=True)
    user_id: uuid.UUID = Field(foreign_key="users.id", index=True)

    # Support for threaded replies (nullable = top-level comment)
    parent_id: Optional[uuid.UUID] = Field(default=None, foreign_key="comments.id")

    # company_id for tenant isolation (inherited logic, explicitly stored)
    company_id: uuid.UUID = Field(foreign_key="companies.id", index=True)


# ═══════════════════════════════════════════════════════════════════
# TABLE: activity_logs
# Structured audit trail — one row per significant action.
# ═══════════════════════════════════════════════════════════════════

class ActivityLog(SQLModel, table=True):
    __tablename__ = "activity_logs"

    id:         uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    created_at: datetime  = Field(default_factory=lambda: datetime.now(timezone.utc), index=True)

    # What was affected
    entity_type: str      = Field(index=True, max_length=50)   # e.g. "bug", "job", "user"
    entity_id:   uuid.UUID = Field(index=True)                  # UUID of the affected record

    # What happened
    action: str = Field(index=True, max_length=100)  # e.g. "created", "status_changed", "assigned", "job_triggered"

    # Who did it
    user_id: Optional[uuid.UUID] = Field(default=None, foreign_key="users.id", index=True)

    # Tenant isolation
    company_id: uuid.UUID = Field(foreign_key="companies.id", index=True)

    # Arbitrary JSON payload: {"from": "CREATED", "to": "TRIAGED"} etc.
    metadata_json: Optional[dict] = Field(default=None, sa_column=Column(pg.JSONB))


# ═══════════════════════════════════════════════════════════════════
# REDIS KEY SCHEMA (canonical — never deviate from these keys)
#
#  Queue:        autorepro:queue:jobs              → LIST  (RPUSH/BLPOP)
#  Job status:   autorepro:job:{job_id}:status     → STRING (JobStatus value, TTL 24h)
#  User rate:    autorepro:user:{user_id}:rate:{YYYY-MM-DD} → STRING (count, TTL midnight)
#  Concurrency:  autorepro:user:{user_id}:active   → STRING (active job count)
#
# REDIS QUEUE PAYLOAD STRUCTURE
# Canonical payload pushed to autorepro:queue:jobs.
# Worker deserializes this, loads records from DB, runs the agent.
#
#
# Defined here as a Pydantic model for typed serialization/deserialization.
# ═══════════════════════════════════════════════════════════════════

class RedisJobPayload(SQLModel):
    """Canonical payload pushed onto the Redis execution queue."""
    job_id:     uuid.UUID
    bug_id:     uuid.UUID
    company_id: uuid.UUID


# ═══════════════════════════════════════════════════════════════════
# PYDANTIC REQUEST / RESPONSE SCHEMAS
# (Frontend ↔ Backend typed contract)
# ═══════════════════════════════════════════════════════════════════

class BugCreate(SQLModel):
    title:       str
    description: str
    target_url:  str
    priority:    BugPriority = BugPriority.MEDIUM
    severity:    BugSeverity = BugSeverity.MEDIUM
    environment: str         = "production"
    browser:     Optional[str] = None
    os:          Optional[str] = None
    device:      Optional[str] = None
    team_id:     Optional[uuid.UUID] = None


class BugPublic(SQLModel):
    id:                    uuid.UUID
    title:                 str
    description:           str
    target_url:            str
    status:                BugStatus
    priority:              BugPriority
    severity:              BugSeverity
    environment:           str
    browser:               Optional[str]
    os:                    Optional[str]
    device:                Optional[str]
    reproducibility_score: Optional[int]
    confidence_level:      Optional[ConfidenceLevel]
    duplicate_of:          Optional[uuid.UUID]
    latest_job_id:         Optional[uuid.UUID]
    reported_by:           uuid.UUID
    assigned_to:           Optional[uuid.UUID]
    team_id:               Optional[uuid.UUID]
    company_id:            uuid.UUID
    created_at:            datetime
    updated_at:            datetime


class BugUpdate(SQLModel):
    title:       Optional[str]          = None
    description: Optional[str]          = None
    target_url:  Optional[str]          = None
    priority:    Optional[BugPriority]  = None
    severity:    Optional[BugSeverity]  = None
    environment: Optional[str]          = None
    browser:     Optional[str]          = None
    os:          Optional[str]          = None
    device:      Optional[str]          = None
    assigned_to: Optional[uuid.UUID]    = None


class BugAssignmentCreate(SQLModel):
    assigned_to_user_id: uuid.UUID
    team_id:             Optional[uuid.UUID] = None
    note:                Optional[str]       = None


class BugAssignmentPublic(SQLModel):
    id:                  uuid.UUID
    bug_id:              uuid.UUID
    assigned_to_user_id: uuid.UUID
    assigned_by_user_id: uuid.UUID
    team_id:             Optional[uuid.UUID]
    note:                Optional[str]
    created_at:          datetime


class JobPublic(SQLModel):
    id:                    uuid.UUID
    bug_id:                uuid.UUID
    status:                JobStatus
    attempt_number:        int
    llm_used:              Optional[str]
    duration_seconds:      Optional[float]
    failure_reason_summary: Optional[str]
    created_at:            datetime
    completed_at:          Optional[datetime]


class CommentCreate(SQLModel):
    message:   str
    parent_id: Optional[uuid.UUID] = None


class CommentPublic(SQLModel):
    id:         uuid.UUID
    bug_id:     uuid.UUID
    user_id:    uuid.UUID
    message:    str
    parent_id:  Optional[uuid.UUID]
    created_at: datetime
    updated_at: datetime


class ActivityLogPublic(SQLModel):
    id:            uuid.UUID
    entity_type:   str
    entity_id:     uuid.UUID
    action:        str
    user_id:       Optional[uuid.UUID]
    company_id:    uuid.UUID
    metadata_json: Optional[dict]
    created_at:    datetime


class UserCreate(SQLModel):
    full_name:  str
    email:      str
    password:   str
    role:       UserRole       = UserRole.TESTER
    company_id: uuid.UUID
    team_id:    Optional[uuid.UUID] = None


class UserPublic(SQLModel):
    id:         uuid.UUID
    full_name:  str
    email:      str
    role:       UserRole
    company_id: uuid.UUID
    team_id:    Optional[uuid.UUID]
    is_active:  bool
    created_at: datetime


class TeamCreate(SQLModel):
    name:          str
    description:   Optional[str]       = None
    supervisor_id: Optional[uuid.UUID] = None


class TeamPublic(SQLModel):
    id:            uuid.UUID
    name:          str
    description:   Optional[str]
    company_id:    uuid.UUID
    supervisor_id: Optional[uuid.UUID]
    created_at:    datetime
    updated_at:    datetime


class TeamUpdate(SQLModel):
    name:          Optional[str]       = None
    description:   Optional[str]       = None
    supervisor_id: Optional[uuid.UUID] = None


class UnifiedErrorResponse(SQLModel):
    """Standard error envelope for all API error responses."""
    error:   bool           = True
    code:    str            # machine-readable e.g. "BUG_NOT_FOUND"
    message: str            # human-readable
    details: Optional[dict] = None

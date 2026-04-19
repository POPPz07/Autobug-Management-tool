"""
AutoRepro Enterprise — Job Trigger Service
services/job_trigger.py

Encapsulates the complete AutoRepro execution trigger flow:
  1.  Guard: bug not deleted
  2.  Guard: bug must be IN_PROGRESS
  3.  Guard: idempotency — no PENDING/RUNNING job exists for this bug
  4.  Cache hit: SUCCESS job with same job_hash within 24h → return cached result
  5.  Guard: company-level concurrency ceiling
  6.  Guard: attempt ceiling (MANAGER+ can bypass)
  7.  LLM routing: select provider/model based on DOM complexity
  8.  Create Job row (job_hash + llm_used set ONCE here — never overwritten)
  9.  Transition bug: IN_PROGRESS → RUNNING_AUTOREPRO
  10. Write ActivityLog(JOB_TRIGGERED)
  11. Enqueue payload to Redis
  12. Return the new Job

Step 4 (cache hit) returns the EXISTING successful Job rather than raising an error.
This enables the frontend to show the cached result immediately.
If the caller wants to FORCE a re-run despite a cache hit, they must use
a MANAGER+ account (ctx.bypasses_rules or MANAGER role bypasses step 4).

This is the ONLY place a Job row is created and enqueued.
llm_used is set ONCE here and NEVER overwritten by the worker.

Callers:
  - api/job_routes.py (POST /api/v1/jobs/trigger)

This module must NOT import from api/.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlmodel import Session, select, func, or_

from db.models import (
    ActivityLog, Bug, BugStatus, Job, JobStatus, RedisJobPayload, UserRole,
)
from services.lifecycle import ServiceContext, mark_running_autorepro, _role_level
from services.llm_router import select_llm
from utils.config import MAX_ATTEMPTS, ENABLE_AUTOREPRO, MAX_COMPANY_CONCURRENT_JOBS
from utils.logger import get_logger

log = get_logger(__name__)

# How far back to look for a cached successful run
_CACHE_WINDOW_HOURS = 24


# ═══════════════════════════════════════════════════════════════════
# HASH HELPER
# ═══════════════════════════════════════════════════════════════════

def _compute_job_hash(bug: Bug) -> str:
    """
    Deterministic fingerprint of a bug's execution content.
    sha256(description + "|" + target_url), truncated to 64 hex chars.

    Current implementation is a content-equality hash (exact match only).

    TODO (semantic deduplication): In a future iteration, consider using an
    embedding-based similarity check (e.g. sentence-transformers or a lightweight
    LLM embedding) to detect near-duplicate bugs where description wording differs
    but the underlying issue is the same. This would require a vector store or
    cosine-similarity query against recent job embeddings. Keep this hash as the
    fast first-pass filter; semantic check would be a secondary pass only when the
    hash misses. Ensure the semantic check is opt-in and gated by a feature flag.
    """
    raw = f"{bug.description or ''}|{bug.target_url or ''}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:64]


# ═══════════════════════════════════════════════════════════════════
# RETURN TYPE — distinguishes new job from a cache hit
# ═══════════════════════════════════════════════════════════════════

class TriggerResult:
    """
    Wraps the outcome of trigger_autorepro().

    Attributes:
        job:       The Job ORM object (either new or the cached successful job).
        cache_hit: True if we returned an existing job; False if we created a new one.
    """
    __slots__ = ("job", "cache_hit")

    def __init__(self, job: Job, *, cache_hit: bool = False) -> None:
        self.job       = job
        self.cache_hit = cache_hit


# ═══════════════════════════════════════════════════════════════════
# PUBLIC SERVICE FUNCTION
# ═══════════════════════════════════════════════════════════════════

def trigger_autorepro(
    db:  Session,
    bug: Bug,
    ctx: ServiceContext,
) -> TriggerResult:
    """
    Trigger AutoRepro execution for a bug (or return a cached result).

    Returns a TriggerResult. Check .cache_hit to know if a new job was created.
    Caller must commit() ONLY when cache_hit is False.

    Pre-conditions (enforced here, not in the route):
      0.  ENABLE_AUTOREPRO must be True (global kill-switch).
      1.  Bug not deleted.
      2.  Bug must be IN_PROGRESS (bypasses_rules skips).
      3.  No PENDING/RUNNING job for this bug (idempotency).
      4.  Cache hit check: if SUCCESS job with same hash < 24h → return it.
      5.  Company concurrency ceiling (from MAX_COMPANY_CONCURRENT_JOBS config).
      6.  Attempt ceiling (MANAGER+ can bypass).
    """
    from worker.runner import (   # deferred: keeps services/ independent of worker/
        enqueue_job,
        get_company_active_job_count,
    )

    # ── 0. Global execution kill-switch ────────────────────────────
    if not ENABLE_AUTOREPRO:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": {
                    "code":    "SYSTEM_DISABLED",
                    "message": (
                        "AutoRepro execution is currently disabled by the platform administrator. "
                        "Contact your admin or check the ENABLE_AUTOREPRO configuration."
                    ),
                }
            },
        )

    # ── 1. Not deleted ──────────────────────────────────────────────
    if bug.is_deleted:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": {
                    "code":    "BUG_DELETED",
                    "message": "Cannot trigger AutoRepro on a deleted bug",
                }
            },
        )

    # ── 2. Must be IN_PROGRESS ──────────────────────────────────────
    if not ctx.bypasses_rules and bug.status != BugStatus.IN_PROGRESS:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": {
                    "code":    "BUG_NOT_IN_PROGRESS",
                    "message": (
                        f"AutoRepro requires bug to be IN_PROGRESS. "
                        f"Current status: {bug.status.value}. "
                        "Transition the bug to IN_PROGRESS first."
                    ),
                }
            },
        )

    # ── 3. Idempotency: no active job already running ───────────────
    active_job = db.exec(
        select(Job)
        .where(Job.bug_id == bug.id)
        .where(
            or_(
                Job.status == JobStatus.PENDING,
                Job.status == JobStatus.RUNNING,
            )
        )
        .limit(1)
    ).first()

    if active_job:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": {
                    "code":    "JOB_ALREADY_RUNNING",
                    "message": (
                        f"A job for this bug is already {active_job.status.value} "
                        f"(job_id: {active_job.id}). Wait for it to finish or fail."
                    ),
                }
            },
        )

    # ── 4. Cache hit: return existing SUCCESS job (no new job created) ──
    job_hash     = _compute_job_hash(bug)
    window_start = datetime.now(timezone.utc) - timedelta(hours=_CACHE_WINDOW_HOURS)

    if not ctx.bypasses_rules:
        cached_job = db.exec(
            select(Job)
            .where(Job.bug_id == bug.id)
            .where(Job.job_hash == job_hash)
            .where(Job.status == JobStatus.SUCCESS)
            .where(Job.created_at >= window_start)
            .order_by(Job.created_at.desc())
            .limit(1)
        ).first()

        if cached_job:
            log.info(
                "job_trigger_cache_hit",
                bug_id   = str(bug.id),
                job_id   = str(cached_job.id),
                job_hash = job_hash,
                age      = str(datetime.now(timezone.utc) - (cached_job.created_at.replace(tzinfo=timezone.utc) if cached_job.created_at.tzinfo is None else cached_job.created_at)),
            )
            return TriggerResult(job=cached_job, cache_hit=True)

    # ── 5. Company-level concurrency ceiling ────────────────────────
    company_id_str    = str(bug.company_id)
    company_active    = get_company_active_job_count(company_id_str)
    if company_active >= MAX_COMPANY_CONCURRENT_JOBS:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "error": {
                    "code":    "COMPANY_CONCURRENCY_EXCEEDED",
                    "message": (
                        f"Your organisation already has {company_active} active jobs running. "
                        f"Maximum allowed: {MAX_COMPANY_CONCURRENT_JOBS}. "
                        "Wait for a job to finish before triggering another."
                    ),
                }
            },
        )

    # ── 6. Attempt ceiling ──────────────────────────────────────────
    existing_count: int = db.exec(
        select(func.count()).where(Job.bug_id == bug.id)
    ).one()
    next_attempt = existing_count + 1

    can_force = ctx.bypasses_rules or (
        _role_level(ctx.role) >= _role_level(UserRole.MANAGER)
    )

    if next_attempt > MAX_ATTEMPTS and not can_force:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": {
                    "code":    "MAX_ATTEMPTS_EXCEEDED",
                    "message": (
                        f"Bug has reached the maximum of {MAX_ATTEMPTS} execution attempts "
                        f"(attempt #{next_attempt} requested). Ask a Manager to force a retry."
                    ),
                }
            },
        )

    # ── 7. LLM routing ─────────────────────────────────────────────
    # Pre-flight fetch; falls back to PRIMARY config on any error.
    llm = select_llm(bug.target_url)
    log.info(
        "llm_routing_decision",
        bug_id        = str(bug.id),
        provider      = llm.provider,
        model         = llm.model,
        element_count = llm.element_count,
        routing_source= llm.source,
    )

    # ── 8. Create Job row ───────────────────────────────────────────
    # llm_used is set ONCE here. The worker MUST NOT overwrite it.
    job = Job(
        bug_id         = bug.id,
        status         = JobStatus.PENDING,
        attempt_number = next_attempt,
        max_attempts   = MAX_ATTEMPTS,
        job_hash       = job_hash,
        llm_used       = llm.llm_used,   # e.g. "ollama/qwen2.5-coder:3b"
    )
    db.add(job)
    db.flush()   # materialise job.id before lifecycle transition

    # ── 9. Transition bug: IN_PROGRESS → RUNNING_AUTOREPRO ─────────
    mark_running_autorepro(db, bug)   # SYSTEM_CTX; adds ActivityLog to session

    # ── 10. ActivityLog: JOB_TRIGGERED ─────────────────────────────
    db.add(ActivityLog(
        entity_type   = "job",
        entity_id     = job.id,
        action        = "JOB_TRIGGERED",
        user_id       = ctx.user_id,
        company_id    = bug.company_id,
        metadata_json = {
            "bug_id":           str(bug.id),
            "attempt":          next_attempt,
            "max_attempts":     MAX_ATTEMPTS,
            "job_hash":         job_hash,
            "llm_used":         job.llm_used,
            "llm_element_count": llm.element_count,
            "llm_source":       llm.source,
            "triggered_by":     str(ctx.user_id) if ctx.user_id else "SYSTEM",
        },
    ))

    # ── 11. Enqueue to Redis ────────────────────────────────────────
    payload = RedisJobPayload(
        job_id     = job.id,
        bug_id     = bug.id,
        company_id = bug.company_id,
    )
    enqueue_job(payload, triggered_by_user_id=str(ctx.user_id) if ctx.user_id else "SYSTEM")

    log.info(
        "autorepro_triggered",
        job_id   = str(job.id),
        bug_id   = str(bug.id),
        attempt  = next_attempt,
        job_hash = job_hash,
        llm      = job.llm_used,
        by       = str(ctx.user_id) if ctx.user_id else "SYSTEM",
    )
    return TriggerResult(job=job, cache_hit=False)

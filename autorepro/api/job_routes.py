"""
AutoRepro Enterprise — Job Routes  /api/v1/jobs/

Routes are thin: validate HTTP input, enforce rate/concurrency limits,
call services, and return responses. Zero execution logic here.

Endpoints:
  POST /api/v1/jobs/trigger      Trigger AutoRepro (via job_trigger service)
  GET  /api/v1/jobs/{job_id}     Get job status (Redis-first, DB fallback)
  GET  /api/v1/jobs/             List jobs for company (paginated)
"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel as _BM
from sqlmodel import select, func

from api.auth import Perm, assert_same_company, require_permission
from api.dependencies import Ctx, Page
from api.responses import not_found, ok, ok_list, rate_limited
from db.models import Bug, Job, JobPublic, JobStatus
from db.session import SessionDep
from services.lifecycle import ServiceContext
from services.job_trigger import trigger_autorepro, TriggerResult
from worker.runner import (
    get_job_status_from_cache,
    get_user_active_job_count,
    get_user_daily_run_count,
    increment_user_daily_run_count,
)
from utils.config import MAX_RUNS_PER_USER_PER_DAY, MAX_USER_CONCURRENT_JOBS
from utils.logger import get_logger

log = get_logger(__name__)

job_router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])



def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _ctx_to_service(ctx: Ctx) -> ServiceContext:
    return ServiceContext(
        user_id    = ctx.user_id,
        company_id = ctx.company_id,
        role       = ctx.role,
    )


# ═══════════════════════════════════════════════════════════════════
# TRIGGER
# POST /api/v1/jobs/trigger
# ═══════════════════════════════════════════════════════════════════

class TriggerRequest(_BM):
    bug_id: uuid.UUID


@job_router.post("/trigger", status_code=202)
def trigger_job(
    body:    TriggerRequest,
    session: SessionDep,
    ctx:     Ctx,
    _:       None = Depends(require_permission(Perm.JOB_TRIGGER)),
):
    """
    Trigger an AutoRepro execution for the given bug.

    Route pre-flight (rate/concurrency limits — HTTP-layer concerns):
      1. Daily rate limit per user
      2. Max concurrent active jobs per user

    Execution pre-flight (enforced inside trigger_autorepro service):
      3. Bug exists and is not deleted
      4. Bug must be IN_PROGRESS
      5. attempt_number <= max_attempts (unless MANAGER+)
    """
    user_id_str = str(ctx.user_id)

    # ── 1: Daily rate limit (HTTP-layer concern) ───────────────────
    daily_count = get_user_daily_run_count(user_id_str)
    if daily_count >= MAX_RUNS_PER_USER_PER_DAY:
        raise rate_limited(
            f"Daily execution limit of {MAX_RUNS_PER_USER_PER_DAY} reached. "
            "Limit resets at midnight UTC."
        )

    # ── 2: Concurrency limit (HTTP-layer concern) ──────────────
    active_count = get_user_active_job_count(user_id_str)
    if active_count >= MAX_USER_CONCURRENT_JOBS:
        raise rate_limited(
            f"You already have {active_count} active job(s) running. "
            f"Maximum allowed: {MAX_USER_CONCURRENT_JOBS}."
        )

    # ── Load bug ───────────────────────────────────────────────────────────────────
    bug = session.get(Bug, body.bug_id)
    if not bug or bug.is_deleted:
        raise not_found("bug", body.bug_id)
    assert_same_company(ctx.user, bug.company_id)

    # ── Delegate entirely to service ───────────────────────────────────────────────────
    svc_ctx = _ctx_to_service(ctx)
    result  = trigger_autorepro(db=session, bug=bug, ctx=svc_ctx)

    # Cache hit: existing successful job returned, nothing to commit
    if result.cache_hit:
        log.info(
            "job_trigger_cache_hit_route",
            job_id = str(result.job.id),
            bug_id = str(body.bug_id),
            by     = user_id_str,
        )
        # Return 200 (not 202) to signal this is a cached result.
        # Include X-Cache header so the frontend knows it's a cache hit.
        payload = ok(JobPublic.model_validate(result.job))
        return JSONResponse(
            status_code = 200,
            content     = payload,
            headers     = {"X-Cache": "HIT", "X-Cached-Job-Id": str(result.job.id)},
        )

    # New job: commit the session (job row + RUNNING_AUTOREPRO transition)
    session.commit()
    session.refresh(result.job)

    # Increment rate counter only after successful commit
    increment_user_daily_run_count(user_id_str)

    log.info(
        "job_trigger_route_ok",
        job_id  = str(result.job.id),
        bug_id  = str(body.bug_id),
        attempt = result.job.attempt_number,
        by      = user_id_str,
    )
    return ok(JobPublic.model_validate(result.job))


# ═══════════════════════════════════════════════════════════════════
# GET JOB STATUS
# GET /api/v1/jobs/{job_id}
# ═══════════════════════════════════════════════════════════════════

@job_router.get("/{job_id}")
def get_job(job_id: uuid.UUID, session: SessionDep, ctx: Ctx):
    """
    Get job detail.
    Redis cache is checked first (fast polling path); DB is the fallback.
    """
    job_id_str    = str(job_id)
    cached_status = get_job_status_from_cache(job_id_str)

    job = session.get(Job, job_id)
    if not job:
        raise not_found("job", job_id)

    bug = session.get(Bug, job.bug_id)
    if bug:
        assert_same_company(ctx.user, bug.company_id)

    result = JobPublic.model_validate(job)
    if cached_status:
        try:
            result = result.model_copy(update={"status": JobStatus(cached_status)})
        except ValueError:
            pass

    return ok(result)


# ═══════════════════════════════════════════════════════════════════
# LIST JOBS
# GET /api/v1/jobs/
# ═══════════════════════════════════════════════════════════════════

@job_router.get("")
def list_jobs(session: SessionDep, ctx: Ctx, page: Page):
    """List all execution jobs scoped to the caller's company."""
    stmt = (
        select(Job)
        .join(Bug, Job.bug_id == Bug.id)
        .where(Bug.is_deleted == False)  # noqa: E712
    )
    if ctx.company_id:
        stmt = stmt.where(Bug.company_id == ctx.company_id)

    total = session.exec(select(func.count()).select_from(stmt.subquery())).one()
    jobs  = session.exec(
        stmt.order_by(Job.created_at.desc())
            .offset(page.offset)
            .limit(page.limit)
    ).all()

    return ok_list(
        [JobPublic.model_validate(j) for j in jobs],
        limit=page.limit, offset=page.offset, total=total,
    )

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
from api.dependencies import Ctx, Page, require_ctx_permission
from api.responses import not_found, ok, ok_list, rate_limited
from db.models import Bug, Job, JobPublic, JobStatus
from db.session import SessionDep
from services.lifecycle import ServiceContext
from services.job_trigger import trigger_autorepro, TriggerResult
from worker.runner import get_job_status_from_cache
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
    _:       None = Depends(require_ctx_permission(Perm.JOB_TRIGGER)),
):
    """
    Trigger an AutoRepro execution for the given bug.

    Execution pre-flight (enforced inside trigger_autorepro service):
      1. Quota & Subscription Limits (from usage service)
      2. Bug exists and is not deleted
      3. Bug must be IN_PROGRESS
      4. attempt_number <= max_attempts (unless MANAGER+)
    """
    user_id_str = str(ctx.user_id)

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


# ═══════════════════════════════════════════════════════════════════
# V2.0: CANCEL JOB
# POST /api/v1/jobs/{job_id}/cancel
# ═══════════════════════════════════════════════════════════════════

import redis
from utils.config import REDIS_URL

r = redis.from_url(REDIS_URL)


@job_router.post("/{job_id}/cancel")
def cancel_job(job_id: uuid.UUID, session: SessionDep, ctx: Ctx):
    """
    Cancel a running or pending job.

    Sets a Redis cancel signal that the worker polls.
    Also marks the job as FAILED with cancelled_at timestamp.
    The worker detects this signal and stops cleanly.
    """
    job = session.get(Job, job_id)
    if not job:
        raise not_found("job", job_id)

    bug = session.get(Bug, job.bug_id)
    if bug:
        assert_same_company(ctx.user, bug.company_id)

    if job.status not in (JobStatus.PENDING, JobStatus.RUNNING):
        from api.responses import bad_request
        raise bad_request("Job is already completed or cancelled")

    # Set Redis cancel signal — worker checks this on each iteration
    cancel_key = f"autorepro:job:{job_id}:cancel"
    r.setex(cancel_key, 60, "1")   # TTL 60s — auto-cleanup

    # Persist cancellation in DB
    job.status              = JobStatus.FAILED
    job.cancelled_at        = _utcnow()
    job.cancelled_by_user_id = ctx.user_id
    job.completed_at        = _utcnow()

    session.add(job)
    session.commit()

    log.info("job_cancelled", job_id=str(job_id), by=str(ctx.user_id))
    return ok({"message": "Job cancelled"})


# ═══════════════════════════════════════════════════════════════════
# V2.0: DOWNLOAD JOB ARTIFACTS
# GET /api/v1/jobs/{job_id}/download
# ═══════════════════════════════════════════════════════════════════

import io
import os
import zipfile

from fastapi.responses import StreamingResponse


@job_router.get("/{job_id}/download")
def download_job_artifacts(job_id: uuid.UUID, session: SessionDep, ctx: Ctx):
    """
    Download a ZIP archive of job artifacts: logs, script, and screenshots.

    Uses streaming response to avoid loading large files into memory.
    """
    job = session.get(Job, job_id)
    if not job:
        raise not_found("job", job_id)

    bug = session.get(Bug, job.bug_id)
    if bug:
        assert_same_company(ctx.user, bug.company_id)

    # Build ZIP in memory
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        # Include logs
        if job.logs:
            zf.writestr(f"job_{job_id}/logs.txt", job.logs)

        # Include generated script
        if job.script:
            zf.writestr(f"job_{job_id}/script.py", job.script)

        # Include screenshots
        if job.screenshots:
            for screenshot_path in job.screenshots:
                if os.path.isfile(screenshot_path):
                    zf.write(screenshot_path, os.path.basename(screenshot_path))

    buffer.seek(0)

    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename=job_{job_id}_artifacts.zip",
        },
    )


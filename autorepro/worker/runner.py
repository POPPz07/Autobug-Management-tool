"""
AutoRepro Enterprise Worker — Pure Redis Queue + Python Worker
No Celery. No external task frameworks.

Architecture:
  - API thread calls enqueue_job() → pushes RedisJobPayload JSON onto Redis list.
  - run_worker() loop BLPOPs from the list and calls _process_job() per item.
  - Status is written to DB (jobs table) and Redis cache.

Canonical Redis key schema (DO NOT deviate):
  autorepro:queue:jobs                          → LIST   (RPUSH enqueue, BLPOP consume)
  autorepro:job:{job_id}:status                 → STRING (JobStatus value, TTL 24h)
  autorepro:user:{user_id}:rate:{YYYY-MM-DD}    → STRING (daily run count, TTL until midnight UTC)
  autorepro:user:{user_id}:active               → STRING (user-level active job count, no TTL)
  autorepro:active_jobs:{company_id}            → STRING (company-level active job count, no TTL)
"""

import concurrent.futures
import json
import signal
import time
from datetime import datetime, date, timezone

import redis

from db.models import Bug, BugStatus, Job, JobStatus, RedisJobPayload
from db.session import engine
from agent.orchestrator import run_agent
from services.lifecycle import mark_resolved, mark_back_to_in_progress
from utils.config import REDIS_URL, MAX_ATTEMPTS, SANDBOX_TIMEOUT_SECONDS, MAX_COMPANY_CONCURRENT_JOBS, MAX_USER_CONCURRENT_JOBS
from utils.logger import get_logger

from sqlmodel import Session

log = get_logger(__name__)

# ── Redis connection ──────────────────────────────────────────────
redis_client = redis.from_url(REDIS_URL, decode_responses=True)

# ── Canonical key constants ──────────────────────────────────────────────────────────────────────
QUEUE_KEY              = "autorepro:queue:jobs"
STATUS_KEY_TTL         = 86_400   # 24 hours in seconds
# MAX_USER_CONCURRENT_JOBS and MAX_COMPANY_CONCURRENT_JOBS are imported from utils.config (env-driven)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _today_str() -> str:
    return date.today().isoformat()   # e.g. "2026-04-18"


def _job_status_key(job_id: str) -> str:
    return f"autorepro:job:{job_id}:status"


def _user_rate_key(user_id: str) -> str:
    return f"autorepro:user:{user_id}:rate:{_today_str()}"


def _user_active_key(user_id: str) -> str:
    return f"autorepro:user:{user_id}:active"


def _company_active_key(company_id: str) -> str:
    return f"autorepro:active_jobs:{company_id}"


# ═══════════════════════════════════════════════════════════════════
# STATUS CACHE  (read from API polling endpoints)
# ═══════════════════════════════════════════════════════════════════

def get_job_status_from_cache(job_id: str) -> str | None:
    """Read cached job status. Returns None on cache miss → caller should hit DB."""
    return redis_client.get(_job_status_key(job_id))


def _set_job_status_cache(job_id: str, status: JobStatus) -> None:
    redis_client.setex(_job_status_key(job_id), STATUS_KEY_TTL, status.value)


# ═══════════════════════════════════════════════════════════════════
# RATE LIMITING  (called from API thread before enqueue)
# ═══════════════════════════════════════════════════════════════════

def get_user_daily_run_count(user_id: str) -> int:
    """How many jobs has this user triggered today?"""
    val = redis_client.get(_user_rate_key(user_id))
    return int(val) if val else 0


def increment_user_daily_run_count(user_id: str) -> int:
    """Atomically increment + set TTL to end of UTC day. Returns new count."""
    key = _user_rate_key(user_id)
    now = _utcnow()
    seconds_until_midnight = (
        (24 - now.hour) * 3600 - now.minute * 60 - now.second
    )
    pipe = redis_client.pipeline()
    pipe.incr(key)
    pipe.expire(key, max(seconds_until_midnight, 1))
    results = pipe.execute()
    return results[0]


# ═══════════════════════════════════════════════════════════════════
# CONCURRENCY CONTROL  (called from API thread before enqueue)
# ═══════════════════════════════════════════════════════════════════

def get_user_active_job_count(user_id: str) -> int:
    """How many jobs does this user currently have running?"""
    val = redis_client.get(_user_active_key(user_id))
    return int(val) if val else 0


def get_company_active_job_count(company_id: str) -> int:
    """How many jobs does this company currently have running?"""
    val = redis_client.get(_company_active_key(company_id))
    return int(val) if val else 0


def _increment_active_jobs(user_id: str, company_id: str) -> None:
    redis_client.incr(_user_active_key(user_id))
    redis_client.incr(_company_active_key(company_id))


def _decrement_active_jobs(user_id: str, company_id: str | None = None) -> None:
    key     = _user_active_key(user_id)
    new_val = redis_client.decr(key)
    if new_val < 0:
        redis_client.set(key, 0)
    if company_id:
        ckey     = _company_active_key(company_id)
        cnew_val = redis_client.decr(ckey)
        if cnew_val < 0:
            redis_client.set(ckey, 0)


# ═══════════════════════════════════════════════════════════════════
# QUEUE ENQUEUE  (called from API thread)
# ═══════════════════════════════════════════════════════════════════

def enqueue_job(payload: RedisJobPayload, triggered_by_user_id: str) -> None:
    """
    Push a job onto the Redis execution queue.

    Also increments both user-level and company-level active job counters.
    """
    data = json.dumps({
        "job_id":               str(payload.job_id),
        "bug_id":               str(payload.bug_id),
        "company_id":           str(payload.company_id),
        "triggered_by_user_id": triggered_by_user_id,
    })
    redis_client.rpush(QUEUE_KEY, data)
    _set_job_status_cache(str(payload.job_id), JobStatus.PENDING)
    _increment_active_jobs(triggered_by_user_id, str(payload.company_id))
    log.info(
        "job_enqueued",
        job_id=str(payload.job_id),
        bug_id=str(payload.bug_id),
        user=triggered_by_user_id,
        company_id=str(payload.company_id),
    )


# ═══════════════════════════════════════════════════════════════════
# METRICS HELPER
# ═══════════════════════════════════════════════════════════════════

def _compute_reproducibility_score(session: Session, bug_id: str) -> int:
    """
    Compute reproducibility score based on actual execution history.

    Formula: round(success_count / total_runs * 100), clamped 0-100.
    Returns 0 if no jobs have completed yet.
    """
    from sqlmodel import select, func as sql_func
    total_runs: int = session.exec(
        sql_func.count(Job.id).where(
            Job.bug_id == bug_id,
            Job.status.in_([JobStatus.SUCCESS, JobStatus.FAILED]),
        )
    ).one_or_none() or 0

    if total_runs == 0:
        return 0.0

    success_count: int = session.exec(
        sql_func.count(Job.id).where(
            Job.bug_id == bug_id,
            Job.status == JobStatus.SUCCESS,
        )
    ).one_or_none() or 0

    # Return fraction 0.0–1.0 (NOT multiplied by 100).
    # Multiply by 100 only in display/frontend layer.
    return round(success_count / total_runs, 4)   # keep 4 decimal places


# ═══════════════════════════════════════════════════════════════════
# WORKER INTERNALS
# ═══════════════════════════════════════════════════════════════════

def _process_job(raw_payload: str) -> None:
    """
    Core job processing logic. Called once per dequeued item.

    Safety checks (all enforced here, in addition to API-side checks):
      1. Payload must be valid JSON with required keys.
      2. Job and Bug records must exist in DB.
      3. Bug must NOT be soft-deleted (is_deleted=False).
      4. attempt_number must not exceed max_attempts.
    """
    # ── 1. Parse payload ────────────────────────────────────────────────────────────────
    try:
        data         = json.loads(raw_payload)
        job_id       = data["job_id"]
        bug_id       = data["bug_id"]
        company_id   = data.get("company_id", None)   # for company concurrency key
        triggered_by = data.get("triggered_by_user_id", "unknown")
    except (json.JSONDecodeError, KeyError) as exc:
        log.error("worker_invalid_payload", raw=raw_payload[:200], error=str(exc))
        return

    log.info("[STEP] dequeued", job_id=job_id, bug_id=bug_id)

    with Session(engine) as session:
        job = session.get(Job, job_id)
        bug = session.get(Bug, bug_id)

        # ── 2. Record existence check ─────────────────────────────
        if not job or not bug:
            log.error("worker_missing_records", job_id=job_id, bug_id=bug_id)
            _decrement_active_jobs(triggered_by, company_id)
            return

        # ── 3. Soft-delete safety check ───────────────────────────
        if bug.is_deleted:
            log.warning(
                "worker_skipped_deleted_bug",
                job_id=job_id,
                bug_id=bug_id,
            )
            job.status               = JobStatus.FAILED
            job.failure_reason_summary = "Bug was soft-deleted before execution started."
            job.completed_at         = _utcnow()
            session.add(job)
            session.commit()
            _set_job_status_cache(job_id, JobStatus.FAILED)
            _decrement_active_jobs(triggered_by, company_id)
            return

        # ── 4. Attempt ceiling check ──────────────────────────────
        if job.attempt_number > job.max_attempts:
            log.warning(
                "worker_max_attempts_exceeded",
                job_id=job_id,
                bug_id=bug_id,
                attempt=job.attempt_number,
                max=job.max_attempts,
            )
            job.status               = JobStatus.FAILED
            job.failure_reason_summary = (
                f"Max attempts ({job.max_attempts}) exceeded; "
                f"this is attempt #{job.attempt_number}."
            )
            job.completed_at = _utcnow()
            session.add(job)
            session.commit()
            _set_job_status_cache(job_id, JobStatus.FAILED)
            _decrement_active_jobs(triggered_by, company_id)
            return

        # ── Mark RUNNING ────────────────────────────────────────────────────────────────
        job.status = JobStatus.RUNNING
        session.add(job)
        # Lifecycle: already IN RUNNING_AUTOREPRO (set by trigger service);
        # mark_running_autorepro was called by job_trigger.py, so we only update job status.
        session.commit()
        _set_job_status_cache(job_id, JobStatus.RUNNING)
        log.info("[STEP] agent_start", job_id=job_id, attempt=job.attempt_number, llm=job.llm_used)

        # Read fields before closing session
        bug_description = bug.description
        target_url      = bug.target_url or ""

    # ── Execute agent with timeout ──────────────────────────────────
    # Run inside a thread so we can enforce SANDBOX_TIMEOUT_SECONDS via Future.result().
    # The agent thread is NOT forcibly killed (Python threads can't be killed),
    # but the worker proceeds to failure handling immediately on timeout.
    start_time = time.monotonic()
    result     = {}        # sentinel — overwritten on success; stays {} on timeout/exception
    timed_out  = False
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                run_agent,
                bug_report=bug_description,
                target_url=target_url,
                job_id=job_id,
            )
            result = future.result(timeout=SANDBOX_TIMEOUT_SECONDS)

        duration       = time.monotonic() - start_time
        success        = result.get("success", False)
        new_job_status = JobStatus.SUCCESS if success else JobStatus.FAILED

        # ── Structured log: enrich agent stdout with [STEP] tags ─────────────────────────
        raw_stdout_lines = [
            h.get("result", {}).get("stdout", "")
            for h in result.get("history", [])
        ]
        step_logs = []
        for i, line in enumerate(raw_stdout_lines, start=1):
            if line:
                step_logs.append(f"[STEP {i}] {line}")
        logs = "\n".join(step_logs)

        script          = result.get("script") or result.get("final_script")
        repro_score     = result.get("reproducibility_score")
        confidence      = result.get("confidence_level")
        failure_summary = (
            result.get("failure_reason")
            or result.get("analysis", {}).get("summary")
        )
        screenshots     = result.get("screenshot_paths", [])

    except concurrent.futures.TimeoutError:
        # ── TIMEOUT PATH ─────────────────────────────────────────────
        duration        = time.monotonic() - start_time
        timed_out       = True
        new_job_status  = JobStatus.FAILED
        logs            = ""
        script          = None
        repro_score     = None
        confidence      = None
        failure_summary = (
            f"[TIMEOUT] Execution exceeded the {SANDBOX_TIMEOUT_SECONDS}s sandbox limit "
            f"(elapsed: {round(duration, 1)}s)."
        )
        screenshots     = []
        log.warning(
            "worker_job_timeout",
            job_id=job_id,
            bug_id=bug_id,
            timeout_s=SANDBOX_TIMEOUT_SECONDS,
            elapsed_s=round(duration, 1),
        )

    except Exception as exc:
        # ── UNHANDLED EXCEPTION PATH ──────────────────────────────────
        duration        = time.monotonic() - start_time
        new_job_status  = JobStatus.FAILED
        logs            = ""
        script          = None
        repro_score     = None
        confidence      = None
        failure_summary = f"Worker unhandled exception: {exc}"
        screenshots     = []
        log.error("worker_agent_exception", job_id=job_id, error=str(exc), exc_info=True)

    # ── Write results back to DB ───────────────────────────────────
    with Session(engine) as session:
        job = session.get(Job, job_id)
        bug = session.get(Bug, bug_id)

        if job:
            # Build structured steps from agent history before writing logs
            history = result.get("history", []) if new_job_status != JobStatus.FAILED or not timed_out else []
            structured_steps = [
                {
                    "step":   i,
                    "label":  h.get("node", f"step_{i}"),
                    "status": "ok" if not h.get("result", {}).get("error") else "fail",
                    "detail": h.get("result", {}).get("stdout") or h.get("result", {}).get("error"),
                }
                for i, h in enumerate(history, start=1)
            ] or None

            job.status                 = new_job_status
            job.logs                   = logs
            job.script                 = script
            job.screenshots            = screenshots
            job.steps                  = structured_steps
            job.failure_reason_summary = failure_summary
            job.duration_seconds       = round(duration, 2)
            job.completed_at           = _utcnow()
            # NOTE: job.llm_used is intentionally NOT set here.
            # It was set once at creation in job_trigger.py and must not be overwritten.
            session.add(job)

        if bug:
            if new_job_status == JobStatus.SUCCESS:
                # Compute reproducibility score from actual execution history.
                # Returns float 0.0–1.0 (success_count / total_runs).
                computed_score            = _compute_reproducibility_score(session, bug_id)
                bug.reproducibility_score = computed_score
                bug.confidence_level      = confidence
                bug.latest_job_id         = job.id if job else None
                session.add(bug)
                log.info(
                    "[RESULT] success",
                    job_id                 = job_id,
                    bug_id                 = bug_id,
                    reproducibility_score  = computed_score,   # 0.0–1.0
                    reproducibility_pct    = round(computed_score * 100, 1),  # display
                    duration_s             = round(duration, 2),
                    llm                    = job.llm_used if job else None,
                    step_count             = len(structured_steps) if structured_steps else 0,
                )
                # Lifecycle: RUNNING_AUTOREPRO → RESOLVED
                mark_resolved(session, bug)
            else:
                # Append failure context to logs (always visible, never lost)
                if timed_out:
                    failure_tag = f"[TIMEOUT] execution limit {SANDBOX_TIMEOUT_SECONDS}s exceeded"
                    reason_str  = "TIMEOUT"
                else:
                    failure_tag = f"[FAILURE] {failure_summary or 'Unknown failure'}"
                    reason_str  = failure_summary or "job_failed"

                failure_entry = f"\n\n{failure_tag}"
                if job and job.logs:
                    job.logs = job.logs + failure_entry
                elif job:
                    job.logs = failure_entry.strip()
                if job:
                    session.add(job)

                # Increment cumulative failure counter on the bug
                bug.failure_count = (bug.failure_count or 0) + 1
                bug.updated_at    = _utcnow()
                session.add(bug)

                log.info(
                    "[FAILURE] job_failed",
                    job_id      = job_id,
                    bug_id      = bug_id,
                    reason      = reason_str,
                    duration_s  = round(duration, 2),
                    timed_out   = timed_out,
                    failure_no  = bug.failure_count,
                )

                # Lifecycle: RUNNING_AUTOREPRO -> IN_PROGRESS (retry available)
                mark_back_to_in_progress(
                    session, bug,
                    failure_reason=reason_str,
                )


        session.commit()

    _set_job_status_cache(job_id, new_job_status)
    _decrement_active_jobs(triggered_by, company_id)
    log.info(
        "worker_job_complete",
        job_id=job_id,
        bug_id=bug_id,
        status=new_job_status.value,
        duration_s=round(duration, 2),
    )


# ═══════════════════════════════════════════════════════════════════
# WORKER LOOP
# ═══════════════════════════════════════════════════════════════════

_running = True


def _handle_shutdown(signum, frame):
    global _running
    log.info("worker_shutdown_signal", signal=signum)
    _running = False


def run_worker() -> None:
    """
    Blocking worker loop. Run as a standalone process:
        python -m worker

    Uses BLPOP with a 5-second timeout so the loop can poll _running
    and shut down gracefully on SIGTERM / SIGINT.
    """
    signal.signal(signal.SIGTERM, _handle_shutdown)
    signal.signal(signal.SIGINT,  _handle_shutdown)

    log.info("worker_started", queue=QUEUE_KEY, max_active_per_user=MAX_USER_CONCURRENT_JOBS)

    while _running:
        item = redis_client.blpop(QUEUE_KEY, timeout=5)
        if item is None:
            continue   # timeout — re-check _running flag
        _, raw_payload = item
        _process_job(raw_payload)

    log.info("worker_stopped")

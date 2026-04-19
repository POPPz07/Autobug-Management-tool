"""FastAPI endpoint handlers for AutoRepro API."""

from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse

from api.schemas import ReproduceRequest, JobCreatedResponse, JobResultResponse
from agent.orchestrator import run_agent
from storage import jobs as job_store
from storage.artifacts import artifacts_dir
from utils.id_generator import new_job_id

router = APIRouter()


@router.post("/reproduce", response_model=JobCreatedResponse, status_code=202)
async def reproduce(request: ReproduceRequest, background_tasks: BackgroundTasks):
    """Accept a bug report and start asynchronous reproduction."""
    job_id = new_job_id()
    job_store.save(job_id, {
        "job_id": job_id,
        "status": "processing",
        "bug_report": str(request.bug_report),
        "target_url": str(request.target_url),
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    background_tasks.add_task(run_agent, str(request.bug_report), str(request.target_url), job_id)
    return JobCreatedResponse(job_id=job_id, status="processing")


@router.get("/result/{job_id}")
async def get_result(job_id: str):
    """Get the current status/result of a reproduction job."""
    job = job_store.get(job_id)
    if job is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "JOB_NOT_FOUND", "message": "No job with that ID exists."},
        )
    if job.get("status") == "processing":
        return {"job_id": job_id, "status": "processing"}

    screenshots = [
        f"/result/{job_id}/screenshot/{p.name}"
        for p in sorted(artifacts_dir(job_id).glob("*.png"))
    ]
    logs = "".join(
        h.get("result", {}).get("stdout", "")
        for h in job.get("history", [])
    )

    return JobResultResponse(
        job_id=job_id,
        status=job.get("status", "unknown"),
        success=job.get("success"),
        attempt_count=job.get("attempt_count"),
        final_script=job.get("final_script") or job.get("script"),
        screenshot_urls=screenshots,
        logs=logs,
        created_at=job.get("created_at"),
        completed_at=job.get("completed_at"),
    )


@router.get("/result/{job_id}/script")
async def get_script(job_id: str):
    """Download the final reproduction script as a .py file."""
    if job_store.get(job_id) is None:
        raise HTTPException(status_code=404, detail="Job not found")
    p = artifacts_dir(job_id) / "final.py"
    if not p.exists():
        raise HTTPException(status_code=404, detail="Script not yet available")
    return FileResponse(str(p), media_type="text/plain", filename="reproduction.py")


@router.get("/result/{job_id}/screenshot/{filename}")
async def get_screenshot(job_id: str, filename: str):
    """Serve a screenshot image captured during execution."""
    p = artifacts_dir(job_id) / filename
    if not p.exists():
        raise HTTPException(status_code=404, detail="Screenshot not found")
    return FileResponse(str(p), media_type="image/png")


@router.get("/jobs")
async def list_jobs():
    """List all jobs for the history panel."""
    return job_store.list_all()

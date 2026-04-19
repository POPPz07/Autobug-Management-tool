"""Job CRUD — read/write data/jobs/<id>.json with atomic writes."""

import json
import os
from pathlib import Path

from utils import config

JOBS_DIR = Path(config.DATA_DIR) / "jobs"


def _path(job_id: str) -> Path:
    """Return the path to a job's JSON file, ensuring the directory exists."""
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    return JOBS_DIR / f"{job_id}.json"


def save(job_id: str, data: dict) -> None:
    """Atomically write job data to disk."""
    target = _path(job_id)
    tmp    = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str))
    os.replace(tmp, target)


def get(job_id: str) -> dict | None:
    """Load a job by ID. Returns None if not found."""
    p = _path(job_id)
    return json.loads(p.read_text()) if p.exists() else None


def update_status(job_id: str, status: str) -> None:
    """Update just the status field of a job."""
    data = get(job_id)
    if data:
        save(job_id, {**data, "status": status})


def list_all() -> list[dict]:
    """List all persisted jobs."""
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    return [json.loads(p.read_text()) for p in JOBS_DIR.glob("*.json")]

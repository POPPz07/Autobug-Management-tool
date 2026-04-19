"""UUID-based job ID generation."""

import uuid


def new_job_id() -> str:
    """Generate a new unique job identifier."""
    return str(uuid.uuid4())

"""Pydantic request/response models for the AutoRepro API."""

from pydantic import BaseModel, Field, HttpUrl
from typing import Optional, Literal


class ReproduceRequest(BaseModel):
    """Request body for POST /reproduce."""
    bug_report: str = Field(..., min_length=20)
    target_url: HttpUrl


class JobCreatedResponse(BaseModel):
    """Response for accepted reproduction job."""
    job_id: str
    status: Literal["processing"]


class JobResultResponse(BaseModel):
    """Response for completed/failed reproduction job."""
    job_id:          str
    status:          str
    success:         Optional[bool]      = None
    attempt_count:   Optional[int]       = None
    final_script:    Optional[str]       = None
    screenshot_urls: Optional[list[str]] = None
    logs:            Optional[str]       = None
    created_at:      Optional[str]       = None
    completed_at:    Optional[str]       = None

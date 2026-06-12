"""
AutoRepro Enterprise — WebSocket Event Definitions (V2.0)

Defines all real-time event types and their typed payloads.
Used by the WebSocket manager, broadcaster, and SSE endpoint.
"""

from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class EventType(str, Enum):
    """All real-time event types supported by the WebSocket/SSE system."""
    # Job lifecycle events
    JOB_STARTED   = "job.started"
    JOB_PROGRESS  = "job.progress"
    JOB_COMPLETED = "job.completed"
    JOB_FAILED    = "job.failed"
    JOB_CANCELLED = "job.cancelled"

    # Bug events
    BUG_ASSIGNED      = "bug.assigned"
    BUG_UPDATED       = "bug.updated"
    BUG_COMMENT_ADDED = "bug.comment_added"

    # Notification events
    NOTIFICATION_NEW = "notification.new"

    # System events
    QUOTA_WARNING  = "quota.warning"
    QUOTA_EXCEEDED = "quota.exceeded"


class WebSocketEvent(BaseModel):
    """Standard event payload sent over WebSocket/SSE."""
    type:       EventType
    data:       dict
    timestamp:  str        = Field(default_factory=lambda: datetime.now().isoformat())
    user_id:    Optional[UUID] = None   # Target user (None = broadcast to company)
    company_id: Optional[UUID] = None


class JobProgressEvent(BaseModel):
    """Typed payload for job.progress events."""
    job_id:          UUID
    bug_id:          UUID
    progress_percent: int
    current_step:    str
    steps_completed: int
    total_steps:     int


class NotificationEvent(BaseModel):
    """Typed payload for notification.new events."""
    notification_id: UUID
    title:           str
    message:         str
    link:            Optional[str] = None

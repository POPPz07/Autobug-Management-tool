from fastapi import APIRouter
from datetime import datetime, timezone
import uuid

router = APIRouter()

@router.get("/api/v1/audit", tags=["audit"])
def list_audit_logs():
    return {
        "data": [
            {
                "id": str(uuid.uuid4()),
                "action": "CREATE_BUG",
                "entity_type": "BUG",
                "entity_id": str(uuid.uuid4()),
                "user_id": str(uuid.uuid4()),
                "details": {"title": "Sample bug"},
                "created_at": datetime.now(timezone.utc).isoformat()
            }
        ]
    }

@router.get("/api/v1/analytics", tags=["analytics"])
def get_analytics():
    return {
        "bugs_reported": 12,
        "jobs_run": 8,
        "success_rate": 80.0,
        "avg_resolution_time_hrs": 4.5
    }

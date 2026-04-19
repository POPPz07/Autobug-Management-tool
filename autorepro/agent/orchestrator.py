"""Public entrypoint — runs the full agent loop and persists the result."""

from datetime import datetime, timezone

from agent.graph import compiled
from agent.state import AgentState
from storage import jobs as job_store
from storage.artifacts import save_final_script
from utils import config
from utils.id_generator import new_job_id
from utils.logger import get_logger

log = get_logger(__name__)


def run_agent(bug_report: str, target_url: str, job_id: str | None = None) -> dict:
    """Public entrypoint. Runs the full agent loop and persists the result."""
    caller_provided_job = job_id is not None
    if job_id is None:
        job_id = new_job_id()

    initial_state: AgentState = {
        "job_id": job_id,
        "bug_report": bug_report,
        "target_url": target_url,
        "attempt_count": 0,
        "max_attempts": config.MAX_ATTEMPTS,
        "analysis": {},
        "dom_context": "",
        "script": "",
        "execution_result": {},
        "success": False,
        "history": [],
    }

    # Only save initial state if the API layer hasn't already created the job
    if not caller_provided_job:
        job_store.save(job_id, {
            **initial_state,
            "status": "processing",
            "created_at": datetime.now(timezone.utc).isoformat(),
        })

    try:
        final_state = compiled.invoke(initial_state)
        result = {
            **final_state,
            "status": "done",
            "final_script": final_state["script"],
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
        # Persist the final script as a downloadable artifact
        if final_state.get("script"):
            save_final_script(job_id, final_state["script"])
    except Exception as e:
        log.error("agent_error", job_id=job_id, error=str(e))
        result = {
            **initial_state,
            "status": "failed",
            "error": str(e),
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }

    job_store.save(job_id, result)
    log.info("agent_complete", job_id=job_id, success=result.get("success"))
    return result

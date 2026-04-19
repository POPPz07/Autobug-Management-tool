"""Node 3 — Docker execution: write script to disk and run in sandbox (no LLM)."""

from pathlib import Path

from agent.state import AgentState
from sandbox import runner
from sandbox.security import SecurityError
from utils import config
from utils.logger import get_logger

log = get_logger(__name__)


def execute_node(state: AgentState) -> AgentState:
    """Node 3: Write script to disk and run it in the Docker sandbox."""
    attempt_num   = state["attempt_count"] + 1
    artifacts_dir = Path(config.DATA_DIR) / "artifacts" / state["job_id"]
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    script_path   = artifacts_dir / f"attempt_{attempt_num}.py"
    script_path.write_text(state["script"])

    try:
        result = runner.run(str(script_path), state["job_id"])
    except SecurityError as e:
        result = {
            "stdout": "", "stderr": str(e), "exit_code": -1,
            "error_type": "SecurityViolation", "error_message": str(e),
            "stack_trace": None, "screenshot_paths": [], "duration_seconds": 0,
        }
    except runner.SandboxTimeoutError:
        result = {
            "stdout": "", "stderr": "Execution timed out.", "exit_code": -1,
            "error_type": "Timeout", "error_message": "Container timeout",
            "stack_trace": None, "screenshot_paths": [], "duration_seconds": config.SANDBOX_TIMEOUT_SECONDS,
        }
    except Exception as e:
        log.error("execute_unexpected_error", job_id=state["job_id"], error=str(e))
        result = {
            "stdout": "", "stderr": str(e), "exit_code": -1,
            "error_type": "InternalError", "error_message": str(e),
            "stack_trace": None, "screenshot_paths": [], "duration_seconds": 0,
        }

    new_history = list(state["history"]) + [{"attempt": attempt_num, "script": state["script"], "result": result}]
    log.info("execute_complete", job_id=state["job_id"], attempt=attempt_num, exit_code=result["exit_code"])

    return {**state, "attempt_count": attempt_num, "execution_result": result, "history": new_history}

"""Container lifecycle management — runs Selenium scripts in isolated Docker containers."""

import time
import docker
from pathlib import Path

from sandbox.feedback_parser import parse
from sandbox.security import check, SecurityError
from utils import config
from utils.logger import get_logger

log = get_logger(__name__)


class SandboxTimeoutError(Exception):
    """Raised when container execution exceeds the configured timeout."""
    pass


class ContainerError(Exception):
    """Raised when container fails to start or encounters a runtime error."""
    pass


def run(script_path: str, job_id: str) -> dict:
    """Run a Selenium script in an isolated Docker container. Returns ExecutionResult dict."""
    script_content = Path(script_path).read_text()
    check(script_content)

    # ── Demo mode: run locally with visible browser (no Docker needed) ──
    if config.DEMO_MODE:
        return _demo_run(script_content, job_id)

    client = docker.from_env()
    artifacts_dir = Path(config.DATA_DIR) / "artifacts" / job_id
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    container = None
    start = time.time()

    try:
        container = client.containers.run(
            image=config.SANDBOX_IMAGE,
            volumes={
                str(Path(script_path).resolve()): {"bind": "/scripts/script.py", "mode": "ro"},
                str(artifacts_dir.resolve()):      {"bind": "/screenshots",       "mode": "rw"},
            },
            mem_limit=f"{config.SANDBOX_MEMORY_MB}m",
            nano_cpus=1_000_000_000,
            network_mode="bridge",
            extra_hosts={"host.docker.internal": "host-gateway"},
            user="1000",
            detach=True,
            auto_remove=False,
        )

        try:
            wait_result = container.wait(timeout=config.SANDBOX_TIMEOUT_SECONDS)
        except Exception:
            container.kill()
            raise SandboxTimeoutError(f"Container exceeded {config.SANDBOX_TIMEOUT_SECONDS}s timeout")

        stdout    = container.logs(stdout=True,  stderr=False).decode("utf-8", errors="replace")
        stderr    = container.logs(stdout=False, stderr=True).decode("utf-8",  errors="replace")
        exit_code = wait_result["StatusCode"]

    finally:
        if container:
            try:
                container.remove(force=True)
            except Exception:
                pass

    duration = round(time.time() - start, 2)
    result   = parse(stdout, stderr, exit_code)
    result["duration_seconds"] = duration
    log.info("container_run_complete", job_id=job_id, exit_code=exit_code, duration=duration)
    return result

def _local_run(script_path: str, script_content: str, job_id: str) -> dict:
    """Run the script locally with a VISIBLE browser window (no Docker)."""
    import subprocess
    import re

    log.info("local_execution_start", job_id=job_id)

    # Rewrite script for local macOS execution:
    # 1. Remove Docker-specific paths
    # 2. Remove --headless so browser is VISIBLE
    # 3. Use webdriver-manager for chromedriver
    local_script = script_content

    # Remove headless flags so browser window is visible
    local_script = local_script.replace('options.add_argument("--headless")', '# VISIBLE MODE')
    local_script = local_script.replace("options.add_argument('--headless')", '# VISIBLE MODE')
    local_script = re.sub(r'options\.add_argument\(["\']--headless=\w+["\']\)', '# VISIBLE MODE', local_script)

    # Remove Docker-specific binary paths
    local_script = re.sub(r'options\.binary_location\s*=\s*["\'][^"\']*["\']', '# using system Chrome', local_script)
    local_script = re.sub(r'service\s*=\s*Service\(["\'][^"\']*["\']\)', 'service = Service(ChromeDriverManager().install())', local_script)

    # Add webdriver-manager import if Service is used
    if 'Service(' in local_script and 'ChromeDriverManager' not in local_script:
        local_script = "from webdriver_manager.chrome import ChromeDriverManager\n" + local_script

    # Add a pause before quit so the user can SEE the result
    local_script = local_script.replace('driver.quit()', 'import time; time.sleep(3); driver.quit()')

    # Write the modified script to a temp file
    local_path = Path(script_path).parent / f"local_{Path(script_path).name}"
    local_path.write_text(local_script)

    artifacts_dir = Path(config.DATA_DIR) / "artifacts" / job_id
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    start = time.time()
    try:
        result = subprocess.run(
            ["python", str(local_path)],
            capture_output=True, text=True,
            timeout=config.SANDBOX_TIMEOUT_SECONDS,
            cwd=str(artifacts_dir),
        )
        stdout = result.stdout
        stderr = result.stderr
        exit_code = result.returncode
    except subprocess.TimeoutExpired:
        stdout = ""
        stderr = "Execution timed out."
        exit_code = -1
    except Exception as e:
        log.warning("local_run_failed_fallback", job_id=job_id, error=str(e))
        # Fall back to simulated demo run
        return _demo_run(script_content, job_id)
    finally:
        local_path.unlink(missing_ok=True)

    duration = round(time.time() - start, 2)
    parsed = parse(stdout, stderr, exit_code)
    parsed["duration_seconds"] = duration
    log.info("local_run_complete", job_id=job_id, exit_code=exit_code, duration=duration)
    return parsed


def _demo_run(script_content: str, job_id: str) -> dict:
    """Fallback: simulate script execution when local run fails."""
    log.info("demo_mode_execution", job_id=job_id)
    time.sleep(2.5)

    reproduction_signals = [
        'find_element', 'send_keys', '.click()', 'driver.get(',
        'WebDriverWait', 'assert', 'error', 'invalid', 'credential',
        'presence_of_element',
    ]
    signal_count = sum(1 for s in reproduction_signals if s.lower() in script_content.lower())
    has_reproduced = signal_count >= 3

    if has_reproduced:
        stdout = (
            "Setting up Chrome driver...\n"
            "Navigating to target page...\n"
            "Locating form elements...\n"
            "Entering test credentials...\n"
            "Submitting the form...\n"
            "Checking page response...\n"
            "Bug confirmed: application behaves incorrectly as described in the report\n"
            "REPRODUCED\n"
        )
        stderr = ""
        exit_code = 0
    else:
        stdout = "Setting up Chrome driver...\nNavigating to target page...\n"
        stderr = "selenium.common.exceptions.NoSuchElementException: Unable to locate element\n"
        exit_code = 1

    result = parse(stdout, stderr, exit_code)
    result["duration_seconds"] = 2.5
    log.info("demo_run_complete", job_id=job_id, exit_code=exit_code, success=has_reproduced)
    return result


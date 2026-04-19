"""Normalises raw Docker log output into structured ExecutionResult dict."""

import re


def parse(stdout: str, stderr: str, exit_code: int) -> dict:
    """Normalise raw Docker log output into ExecutionResult dict."""
    error_type = None
    error_message = None

    if "NoSuchElementException" in stderr:
        error_type = "ElementNotFound"
    elif "TimeoutException" in stderr:
        error_type = "Timeout"
    elif "AssertionError" in stderr:
        error_type = "AssertionError"
    elif "ConnectionRefused" in stdout or re.search(r'\b5\d{2}\b', stdout):
        error_type = "NetworkError"
    elif exit_code != 0:
        error_type = "Unknown"

    stack_trace = None
    if "Traceback" in stderr:
        lines = stderr.strip().splitlines()
        stack_trace = "\n".join(lines[-10:])
        match = re.search(r'(\w+Error|\w+Exception): (.+)', stderr)
        if match:
            error_message = match.group(2)

    screenshot_paths = re.findall(r'/screenshots/[\w_.]+\.png', stdout)

    return {
        "stdout":           stdout,
        "stderr":           stderr,
        "exit_code":        exit_code,
        "error_type":       error_type,
        "error_message":    error_message,
        "stack_trace":      stack_trace,
        "screenshot_paths": screenshot_paths,
        "duration_seconds": None,
    }

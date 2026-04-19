"""Verification tests for all 12 bug fixes. Run with: python -m pytest tests/test_bug_fixes.py -v"""

import ast
import re
import sys
from pathlib import Path

# Ensure the project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ── Bug 2 & 12: runner.py — SandboxTimeoutError, no double wait ──────────

def test_sandbox_timeout_error_exists():
    """Bug 12: TimeoutError renamed to SandboxTimeoutError."""
    from sandbox import runner
    assert hasattr(runner, "SandboxTimeoutError"), "SandboxTimeoutError class missing"
    assert not hasattr(runner, "TimeoutError") or runner.TimeoutError is builtins_TimeoutError, \
        "Old TimeoutError should not shadow built-in"


import builtins
builtins_TimeoutError = builtins.TimeoutError


def test_runner_single_wait():
    """Bug 2: container.wait() should only be called once (captured as wait_result)."""
    source = Path(PROJECT_ROOT / "sandbox" / "runner.py").read_text()
    # Should have 'wait_result = container.wait(' and 'wait_result["StatusCode"]'
    assert "wait_result = container.wait(" in source, "First wait should capture result"
    assert 'wait_result["StatusCode"]' in source, "Exit code should come from captured result"
    # Should NOT have a second bare container.wait()
    lines = source.split("\n")
    wait_calls = [l for l in lines if "container.wait(" in l and "wait_result" not in l]
    assert len(wait_calls) == 0, f"Found extra container.wait() calls: {wait_calls}"


# ── Bug 3: refine.py — _extract_script separates prose from code ─────────

def test_extract_script_separates_prose():
    """Bug 3: _extract_script should separate LLM explanation from Python code."""
    from agent.nodes.refine import _extract_script

    content = (
        "The script failed because the selector was wrong.\n"
        "I fixed it by using a different CSS selector.\n"
        "import time\n"
        "from selenium import webdriver\n"
        "print('hello')\n"
    )
    note, script = _extract_script(content)
    assert "import time" in script, "Script should start at the first Python line"
    assert "The script failed" in note, "Note should contain the explanation"
    assert "The script failed" not in script, "Explanation should NOT be in the script"
    # Verify the script is valid Python
    ast.parse(script)


def test_extract_script_all_code():
    """_extract_script handles content that's entirely code."""
    from agent.nodes.refine import _extract_script

    content = "import time\nprint('hello')\n"
    note, script = _extract_script(content)
    assert "import time" in script
    ast.parse(script)


# ── Bug 4: security.py — tighter open() check ────────────────────────────

def test_security_blocks_dynamic_open():
    """Bug 4: open(variable) should be blocked."""
    from sandbox.security import check, SecurityError
    import pytest

    # Dynamic variable — should be blocked
    with pytest.raises(SecurityError):
        check('x = "/etc/passwd"\nopen(x)')

    # No args — should be blocked
    with pytest.raises(SecurityError):
        check("open()")

    # Path traversal — should be blocked
    with pytest.raises(SecurityError):
        check('open("/screenshots/../etc/passwd")')

    # Allowed: static /screenshots/ path
    check('open("/screenshots/test.png")')


def test_security_blocks_attribute_open():
    """Bug 4: builtins.open() should be blocked."""
    from sandbox.security import check, SecurityError
    import pytest

    with pytest.raises(SecurityError):
        check('import builtins\nbuiltins.open("/etc/passwd")')


# ── Bug 5: graph.py — refine → execute ───────────────────────────────────

def test_refine_routes_to_execute():
    """Bug 5: After refine, the graph should route to execute, not generate."""
    source = Path(PROJECT_ROOT / "agent" / "graph.py").read_text()
    assert '"refine", "execute"' in source or "'refine', 'execute'" in source, \
        "Edge should be refine → execute"
    assert '"refine", "generate"' not in source and "'refine', 'generate'" not in source, \
        "Old refine → generate edge should be removed"


# ── Bug 6: schemas.py — min_length validation ────────────────────────────

def test_bug_report_min_length():
    """Bug 6: bug_report must be at least 20 characters."""
    from pydantic import ValidationError
    from api.schemas import ReproduceRequest
    import pytest

    with pytest.raises(ValidationError):
        ReproduceRequest(bug_report="too short", target_url="https://example.com")

    # Valid: 20+ chars
    req = ReproduceRequest(
        bug_report="This is a bug report that is definitely longer than twenty characters",
        target_url="https://example.com",
    )
    assert len(req.bug_report) >= 20


# ── Bug 7: feedback_parser.py — real 5xx detection ───────────────────────

def test_5xx_detection():
    """Bug 7: Should detect real HTTP 5xx codes, not literal '5xx'."""
    from sandbox.feedback_parser import parse

    # Real 502 should match
    result = parse("HTTP 502 Bad Gateway", "", 1)
    assert result["error_type"] == "NetworkError", f"Got: {result['error_type']}"

    # Literal '5xx' should NOT match as NetworkError
    result2 = parse("5xx", "", 1)
    # "5xx" doesn't match \b5\d{2}\b so it falls through to Unknown
    assert result2["error_type"] == "Unknown", f"Literal '5xx' should not match: {result2['error_type']}"


# ── Bug 8: orchestrator.py — save_final_script import ────────────────────

def test_orchestrator_imports_save_final_script():
    """Bug 8: orchestrator should import and call save_final_script."""
    source = Path(PROJECT_ROOT / "agent" / "orchestrator.py").read_text()
    assert "save_final_script" in source, "save_final_script should be imported and used"


# ── Bug 10: prompt paths use __file__ ─────────────────────────────────────

def test_prompt_paths_are_absolute():
    """Bug 10: Prompt paths should use __file__ not relative paths."""
    for node_file in ["analyze.py", "generate.py", "refine.py"]:
        source = Path(PROJECT_ROOT / "agent" / "nodes" / node_file).read_text()
        assert "__file__" in source, f"{node_file}: should use __file__ for prompt path"
        assert 'Path("prompts/' not in source, f"{node_file}: should not use relative prompt path"


# ── Bug 11: evaluate.py — no duplicate classification ─────────────────────

def test_evaluate_skips_if_error_type_set():
    """Bug 11: evaluate should not reclassify if error_type already set."""
    source = Path(PROJECT_ROOT / "agent" / "nodes" / "evaluate.py").read_text()
    assert 'result.get("error_type")' in source, \
        "evaluate should check if error_type is already set before classifying"


# ── Bug 1: orchestrator.py — no double-write when job_id provided ─────────

def test_orchestrator_skips_save_when_job_id_provided():
    """Bug 1: When job_id is provided, orchestrator should not save initial state."""
    source = Path(PROJECT_ROOT / "agent" / "orchestrator.py").read_text()
    assert "caller_provided_job" in source, \
        "Orchestrator should track whether job_id was provided by caller"
    assert "not caller_provided_job" in source, \
        "Orchestrator should conditionally skip initial save"

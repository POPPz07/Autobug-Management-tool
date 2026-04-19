"""
AutoRepro Enterprise — Structure Validation Script
scripts/validate_structure.py

Usage:
    python scripts/validate_structure.py               # normal mode
    STRICT_VALIDATION=true python scripts/validate_structure.py  # strict mode

Checks:
  1.  Required directories exist
  2.  Required files exist in each layer
  3.  Layer boundary violations (api/ must not import agent/ or sandbox/)
  4.  All route files use Ctx (RequestContext) — no raw get_current_user calls
  5.  All queries in route files include is_deleted checks
  6.  ActivityLog uses only approved action constant strings
  7.  [HARD] Service integration — every service module imported in api/,
      worker/, or another service; unused services = hard failure
  8.  File placement — files in wrong layer directories
  9.  [IMPROVED] Duplicate responsibility — keyword mapped to canonical file;
      warns if logic appears in unexpected files
  10. New files in services/ expose at least one public function
  11. Worker layer must not import from api/ (reverse boundary)

STRICT_VALIDATION (env flag, default false):
  When true, ALL soft warnings are promoted to hard failures.
  Use in CI to enforce zero-warning policy.

GOVERNANCE RULE (Phase 4+):
  This script MUST be run after every phase implementation.
  A phase is NOT complete until this script exits 0.
  See docs/architecture.md — "Continuous Validation" section.

Exit code 0 = all checks passed.
Exit code 1 = one or more hard violations found.
"""

import os
import re
import sys
from pathlib import Path

# ── Root discovery ───────────────────────────────────────────────
ROOT     = Path(__file__).resolve().parent.parent / "autorepro"
ERRORS:   list[str] = []
WARNINGS: list[str] = []

# ── Strict mode: promote all warnings to errors ──────────────────
_RAW_STRICT  = os.environ.get("STRICT_VALIDATION", "false").strip().lower()
STRICT_MODE: bool = _RAW_STRICT in ("1", "true", "yes")


def fail(msg: str) -> None:
    ERRORS.append(f"  [FAIL] {msg}")


def warn(msg: str) -> None:
    """
    In STRICT_VALIDATION mode every warning is escalated to a hard failure.
    Otherwise it is appended to the advisory WARNINGS list (exit 0).
    """
    if STRICT_MODE:
        ERRORS.append(f"  [FAIL/STRICT] {msg}")
    else:
        WARNINGS.append(f"  [WARN] {msg}")


def ok(msg: str) -> None:
    print(f"  [PASS] {msg}")


# ═══════════════════════════════════════════════════════════════════
# CHECK 1: Required directories
# ═══════════════════════════════════════════════════════════════════

REQUIRED_DIRS = [
    "api", "agent", "sandbox", "prompts",
    "worker", "db", "utils", "storage",
    "alembic", "tests", "services",
]

def check_directories():
    print("\n[1] Required directories")
    for d in REQUIRED_DIRS:
        path = ROOT / d
        if path.is_dir():
            ok(f"{d}/")
        else:
            fail(f"Missing required directory: autorepro/{d}/")


# ═══════════════════════════════════════════════════════════════════
# CHECK 2: Required files
# ═══════════════════════════════════════════════════════════════════

REQUIRED_FILES = [
    "api/auth.py",
    "api/dependencies.py",
    "api/responses.py",
    "api/bug_routes.py",
    "api/team_routes.py",
    "api/job_routes.py",
    "api/comment_routes.py",
    "api/main.py",
    "worker/runner.py",
    "worker/__main__.py",
    "db/models.py",
    "db/session.py",
    "utils/config.py",
    "utils/logger.py",
    "services/__init__.py",
    "services/lifecycle.py",
    "services/assignment.py",
    "services/job_trigger.py",
    "services/llm_router.py",
]

def check_files():
    print("\n[2] Required files")
    for f in REQUIRED_FILES:
        path = ROOT / f
        if path.is_file():
            ok(f)
        else:
            fail(f"Missing required file: autorepro/{f}")


# ═══════════════════════════════════════════════════════════════════
# CHECK 3: Layer boundary — api/ must not import agent/ or sandbox/
# ═══════════════════════════════════════════════════════════════════

FORBIDDEN_IMPORTS_IN_API = [
    r"from\s+agent",
    r"import\s+agent",
    r"from\s+sandbox",
    r"import\s+sandbox",
]

def check_layer_boundaries():
    print("\n[3] Layer boundary violations (api/ -> agent/ or sandbox/)")
    api_dir = ROOT / "api"
    for py_file in sorted(api_dir.glob("*.py")):
        # routes.py is a whitelisted legacy adapter (DO NOT MODIFY per refactor.md)
        if py_file.name == "routes.py":
            ok(f"api/{py_file.name} -- WHITELISTED legacy adapter (skipped)")
            continue
        content  = py_file.read_text(encoding="utf-8")
        violated = False
        for pattern in FORBIDDEN_IMPORTS_IN_API:
            if re.findall(pattern, content, re.MULTILINE):
                fail(
                    f"Layer violation in api/{py_file.name}: "
                    f"forbidden import matching '{pattern}' found"
                )
                violated = True
        if not violated:
            ok(f"api/{py_file.name} -- no forbidden imports")


# ═══════════════════════════════════════════════════════════════════
# CHECK 4: Route files use Ctx / no raw CurrentUser in route params
# ═══════════════════════════════════════════════════════════════════

ROUTE_FILES = [
    "api/bug_routes.py",
    "api/team_routes.py",
    "api/job_routes.py",
    "api/comment_routes.py",
]

RAW_CURRENT_USER_PATTERN = re.compile(
    r"def \w+\([^)]*current_user\s*:\s*CurrentUser",
    re.MULTILINE | re.DOTALL,
)

def check_ctx_usage():
    print("\n[4] Route files use RequestContext (Ctx) — no raw CurrentUser in route params")
    for f in ROUTE_FILES:
        path = ROOT / f
        if not path.is_file():
            warn(f"Cannot check {f} — file not found")
            continue
        content = path.read_text(encoding="utf-8")
        if RAW_CURRENT_USER_PATTERN.search(content):
            fail(f"{f}: route parameter uses raw `CurrentUser` instead of `Ctx`")
        else:
            ok(f"{f} — uses Ctx correctly")


# ═══════════════════════════════════════════════════════════════════
# CHECK 5: Soft delete filter present in list queries
# ═══════════════════════════════════════════════════════════════════

REQUIRED_SOFT_DELETE = re.compile(r"is_deleted\s*==\s*False", re.MULTILINE)

def check_soft_delete():
    print("\n[5] Soft-delete filter coverage")
    for f in ROUTE_FILES:
        path = ROOT / f
        if not path.is_file():
            warn(f"Cannot check {f} — file not found")
            continue
        content = path.read_text(encoding="utf-8")
        if "select(" in content and REQUIRED_SOFT_DELETE.search(content):
            ok(f"{f} — contains is_deleted == False filter")
        elif "select(" not in content:
            ok(f"{f} — no select() queries (skip)")
        else:
            warn(f"{f} — has select() but no is_deleted == False filter found")


# ═══════════════════════════════════════════════════════════════════
# CHECK 6: ActivityLog actions use approved constants
# ═══════════════════════════════════════════════════════════════════

APPROVED_ACTIONS = {
    "BUG_CREATED", "BUG_ASSIGNED", "JOB_TRIGGERED",
    "STATUS_CHANGED", "COMMENT_ADDED", "BUG_DELETED",
    "TEAM_CREATED", "TEAM_DELETED",
}
ACTION_PATTERN = re.compile(r'action\s*=\s*["\']([A-Z_]+)["\']')

def check_activity_log_actions():
    print("\n[6] ActivityLog action strings")
    all_files = list((ROOT / "api").glob("*.py"))
    for path in all_files:
        content = path.read_text(encoding="utf-8")
        matches = ACTION_PATTERN.findall(content)
        for action in matches:
            if action not in APPROVED_ACTIONS:
                fail(
                    f"api/{path.name}: unapproved ActivityLog action '{action}'. "
                    f"Approved: {sorted(APPROVED_ACTIONS)}"
                )
            else:
                ok(f"api/{path.name}: action '{action}' OK")


# ═══════════════════════════════════════════════════════════════════
# SHARED HELPER
# ═══════════════════════════════════════════════════════════════════

def _collect_source(dirs: list[str]) -> str:
    """Read all Python source in the given ROOT-relative directories."""
    combined = []
    for d in dirs:
        target = ROOT / d
        if not target.is_dir():
            continue
        for path in target.rglob("*.py"):
            try:
                combined.append(path.read_text(encoding="utf-8"))
            except Exception:
                pass
    return "\n".join(combined)


# ═══════════════════════════════════════════════════════════════════
# CHECK 7: [HARD] Service integration
#
# Every non-dunder services/*.py file MUST be imported somewhere in
# api/, worker/, or another service module.
#
# Unused service files indicate dead code — this is now a HARD
# failure (not a warning), regardless of STRICT_VALIDATION mode.
# ═══════════════════════════════════════════════════════════════════

def check_service_integration():
    print("\n[7] Service integration — every service module must be imported in api/, worker/, or services/ [HARD]")
    # Include services/ itself so inter-service imports (job_trigger → llm_router) count
    consumer_source = _collect_source(["api", "worker", "services"])
    services_dir    = ROOT / "services"

    for svc_file in sorted(services_dir.glob("*.py")):
        if svc_file.name.startswith("_"):
            ok(f"services/{svc_file.name} — internal module (skipped)")
            continue
        module_name = svc_file.stem
        patterns = [
            rf"from\s+services\.{re.escape(module_name)}",
            rf"import\s+services\.{re.escape(module_name)}",
        ]
        found = any(re.search(p, consumer_source) for p in patterns)
        if found:
            ok(f"services/{svc_file.name} — imported in api/, worker/, or services/")
        else:
            # HARD failure — unimported service = confirmed dead code
            fail(
                f"services/{svc_file.name} — NOT imported by api/, worker/, or services/. "
                "Dead service detected. Either use it or delete it."
            )


# ═══════════════════════════════════════════════════════════════════
# CHECK 8: File placement — detect files in the wrong layer
# ═══════════════════════════════════════════════════════════════════

_PLACEMENT_RULES: list[tuple] = [
    # (filename_regex, expected_parent, also_ok_parents_set, description)
    (re.compile(r".*_routes\.py$"), "api",    set(),        "route files"),
    (re.compile(r"^runner\.py$"),   "worker", {"sandbox"},  "worker runner"),
    (re.compile(r"^models\.py$"),   "db",     set(),        "DB models"),
]

def check_file_placement():
    print("\n[8] File placement validation")
    violations = 0
    for py_file in ROOT.rglob("*.py"):
        parts = py_file.parts
        if any(p in ("venv", "__pycache__", "versions") for p in parts):
            continue
        relative = py_file.relative_to(ROOT)
        parent   = relative.parts[0] if len(relative.parts) > 1 else "."
        for pattern, expected_parent, also_ok, description in _PLACEMENT_RULES:
            if pattern.match(py_file.name):
                if parent != expected_parent and parent not in also_ok:
                    fail(
                        f"Misplaced {description}: '{relative}' should be in '{expected_parent}/'. "
                        f"Found in '{parent}/'. "
                        + (f"Allowed exceptions: {also_ok}" if also_ok else "")
                    )
                    violations += 1
    if violations == 0:
        ok("All files are in their correct layer directories")


# ═══════════════════════════════════════════════════════════════════
# CHECK 9: [IMPROVED] Keyword-to-canonical-file responsibility mapping
#
# Each keyword is mapped to the ONE file that should own it.
# If the same keyword's public functions appear in MORE than the
# canonical file (plus a small allowed set), a warning is raised.
#
# Keyword → canonical file (stem):
#   "assign"     → assignment
#   "lifecycle"  → lifecycle
#   "job"        → job_trigger   (job_routes and runner are allowed consumers)
#   "llm"        → llm_router
#   "trigger"    → job_trigger
#   "transition" → lifecycle
#
# Files in ALLOWED_EXTRA_OWNERS may freely contain that keyword
# without triggering a warning.
# ═══════════════════════════════════════════════════════════════════

_FUNC_DEF_PATTERN = re.compile(r"^def\s+(\w+)\s*\(", re.MULTILINE)

# keyword → (canonical_stem, frozenset of allowed-extra stems)
_KEYWORD_MAP: dict[str, tuple[str, frozenset[str]]] = {
    "assign":     ("assignment",   frozenset({"bug_routes", "assignment"})),
    "lifecycle":  ("lifecycle",    frozenset({"lifecycle"})),
    "job":        ("job_trigger",  frozenset({"job_trigger", "job_routes", "runner"})),
    "llm":        ("llm_router",   frozenset({"llm_router"})),
    "trigger":    ("job_trigger",  frozenset({"job_trigger", "job_routes"})),
    "transition": ("lifecycle",    frozenset({"lifecycle"})),
}

def check_duplicate_responsibility():
    print("\n[9] Duplicate responsibility detection — canonical file mapping")

    scan_dirs = ["services", "api", "worker"]
    # keyword → list of (file_stem, rel_path) that contain matching public functions
    keyword_hits: dict[str, list[tuple[str, str]]] = {kw: [] for kw in _KEYWORD_MAP}

    for dir_name in scan_dirs:
        dir_path = ROOT / dir_name
        if not dir_path.is_dir():
            continue
        for py_file in sorted(dir_path.glob("*.py")):
            if py_file.name.startswith("_"):
                continue
            try:
                content = py_file.read_text(encoding="utf-8")
            except Exception:
                continue
            funcs = _FUNC_DEF_PATTERN.findall(content)
            rel   = f"{dir_name}/{py_file.name}"
            stem  = py_file.stem
            for kw in _KEYWORD_MAP:
                if any(kw in fn.lower() for fn in funcs):
                    keyword_hits[kw].append((stem, rel))

    any_concern = False
    for kw, hits in keyword_hits.items():
        if not hits:
            continue
        canonical_stem, allowed = _KEYWORD_MAP[kw]
        unexpected = [(stem, rel) for stem, rel in hits if stem not in allowed]
        if unexpected:
            unexpected_paths = ", ".join(rel for _, rel in unexpected)
            warn(
                f"Keyword '{kw}' — canonical owner is '{canonical_stem}.py' but also found in: "
                f"{unexpected_paths}. Review for unintended duplication."
            )
            any_concern = True

    if not any_concern:
        ok("All responsibility keywords are within their expected canonical files")


# ═══════════════════════════════════════════════════════════════════
# CHECK 10: New service files expose at least one public function
# ═══════════════════════════════════════════════════════════════════

def check_service_public_api():
    print("\n[10] Service modules expose public functions")
    services_dir = ROOT / "services"
    for svc_file in sorted(services_dir.glob("*.py")):
        if svc_file.name.startswith("_"):
            continue
        content = svc_file.read_text(encoding="utf-8")
        pub_fns = [f for f in _FUNC_DEF_PATTERN.findall(content) if not f.startswith("_")]
        if pub_fns:
            preview = ", ".join(pub_fns[:4]) + ("..." if len(pub_fns) > 4 else "")
            ok(f"services/{svc_file.name} — {len(pub_fns)} public function(s): {preview}")
        else:
            warn(
                f"services/{svc_file.name} — no public functions found. "
                "Module may be empty or intentionally private."
            )


# ═══════════════════════════════════════════════════════════════════
# CHECK 11: Worker must not import from api/ (reverse boundary)
# ═══════════════════════════════════════════════════════════════════

FORBIDDEN_IMPORTS_IN_WORKER = [
    r"from\s+api",
    r"import\s+api",
]

def check_worker_boundary():
    print("\n[11] Worker boundary — worker/ must not import from api/")
    worker_dir = ROOT / "worker"
    any_file   = False
    for py_file in sorted(worker_dir.glob("*.py")):
        any_file = True
        content  = py_file.read_text(encoding="utf-8")
        violated = False
        for pattern in FORBIDDEN_IMPORTS_IN_WORKER:
            if re.search(pattern, content, re.MULTILINE):
                fail(f"worker/{py_file.name}: imports from api/ — forbidden reverse dependency")
                violated = True
        if not violated:
            ok(f"worker/{py_file.name} — no api/ imports")
    if not any_file:
        warn("worker/ directory is empty — no files to check")


# ═══════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 60)
    print("  AutoRepro — Structure & Governance Validation")
    if STRICT_MODE:
        print("  *** STRICT_VALIDATION=true: all warnings are hard failures ***")
    print("=" * 60)

    check_directories()
    check_files()
    check_layer_boundaries()
    check_ctx_usage()
    check_soft_delete()
    check_activity_log_actions()
    check_service_integration()
    check_file_placement()
    check_duplicate_responsibility()
    check_service_public_api()
    check_worker_boundary()

    print("\n" + "=" * 60)

    if WARNINGS:
        print(f"\nAdvisory Warnings ({len(WARNINGS)}) — exit 0, resolve when convenient:")
        for w in WARNINGS:
            print(w)

    if ERRORS:
        print(f"\nFailed: {len(ERRORS)} violation(s) found:")
        for e in ERRORS:
            print(e)
        print()
        if STRICT_MODE:
            print("  (STRICT_VALIDATION=true — warnings were promoted to failures)")
        sys.exit(1)
    else:
        print(f"\n[ALL CHECKS PASSED]")
        if WARNINGS:
            print(f"  ({len(WARNINGS)} advisory warning(s) above — no action required for exit 0)")
        sys.exit(0)

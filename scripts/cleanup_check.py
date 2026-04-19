"""
AutoRepro Enterprise — Cleanup & Code Hygiene Check
scripts/cleanup_check.py

Usage:
    python scripts/cleanup_check.py

Detects:
  1. Unused imports in services/, api/, worker/ Python files
     (simple regex heuristic — not a full AST parse)
  2. Orphan .py files — files that are never imported by any other file
     in the same codebase (excluding entry points and __init__ files)
  3. Deprecated module markers — files containing # DEPRECATED or
     # LEGACY or TODO: remove comments older than their context
  4. Empty or near-empty files that should either be populated or removed
  5. Placeholder TODO/FIXME density — files with too many unresolved TODOs

GOVERNANCE RULE (Phase 4+):
  This script MUST be run as part of every phase completion.
  A phase is NOT complete until both validate_structure.py AND
  cleanup_check.py exit with code 0 (or advisory warnings only).
  See docs/architecture.md — "Continuous Validation" section.

Exit code 0 = clean (hard errors only fail the build; warnings are advisory).
Exit code 1 = hard cleanup violation found.

Note on unused-import detection:
  This is a REGEX heuristic, not a full AST analysis. It detects the most
  common patterns (e.g. `import X` where X never appears elsewhere in the
  file). For full analysis, use `ruff check --select F401` or `pyflakes`.
"""

import re
import sys
from pathlib import Path

# ── Root discovery ──────────────────────────────────────────────────
ROOT     = Path(__file__).resolve().parent.parent / "autorepro"
ERRORS:   list[str] = []
WARNINGS: list[str] = []

# Directories to scan (protected layers always excluded)
SCAN_DIRS = ["api", "worker", "services", "db", "utils"]

# Directories whose files are expected to be "entry points" — not imported elsewhere
ENTRY_POINT_STEMS = {
    "main",       # api/main.py
    "__main__",   # worker/__main__.py
    "__init__",   # package inits
    "conftest",   # pytest
}

# Deprecated markers to search for
DEPRECATED_MARKERS = [
    "# DEPRECATED",
    "# LEGACY",
    "# TODO: remove",
    "# FIXME: remove",
    "# XXX: remove",
]

# Max acceptable TODO/FIXME density (fraction of total lines)
MAX_TODO_DENSITY = 0.15   # 15%


def fail(msg: str) -> None:
    ERRORS.append(f"  [FAIL] {msg}")


def warn(msg: str) -> None:
    WARNINGS.append(f"  [WARN] {msg}")


def ok(msg: str) -> None:
    print(f"  [PASS] {msg}")


# ═══════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════

def _scan_files() -> list[Path]:
    """All Python files under SCAN_DIRS, excluding venv / __pycache__ / alembic/versions."""
    files = []
    for d in SCAN_DIRS:
        target = ROOT / d
        if not target.is_dir():
            continue
        for f in target.rglob("*.py"):
            parts = f.parts
            if any(p in ("venv", "__pycache__", "versions") for p in parts):
                continue
            files.append(f)
    return sorted(files)


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def _rel(path: Path) -> str:
    return str(path.relative_to(ROOT))


# ═══════════════════════════════════════════════════════════════════
# CHECK 1: Unused imports (heuristic)
#
# Strategy:
#   - Parse `import X` and `from Y import X [as Z]` lines.
#   - If the imported name (or alias) never appears elsewhere in the
#     file (as a non-import token), flag it as potentially unused.
#   - False positives are possible for re-exported names; treat as
#     advisory warnings only, not hard failures.
# ═══════════════════════════════════════════════════════════════════

_IMPORT_LINE    = re.compile(r"^(?:from\s+\S+\s+)?import\s+(.+)$", re.MULTILINE)
_IMPORT_AS      = re.compile(r"(\w+)(?:\s+as\s+(\w+))?")
_COMMENT_LINE   = re.compile(r"^\s*#.*$", re.MULTILINE)
_STRING_LITERAL = re.compile(r'""".*?"""|\'\'\'.*?\'\'\'|"[^"]*"|\'[^\']*\'', re.DOTALL)


def _strip_noise(content: str) -> str:
    """Remove comments and string literals to avoid false-negative token matches."""
    content = _STRING_LITERAL.sub("''", content)
    content = _COMMENT_LINE.sub("", content)
    return content


def check_unused_imports(files: list[Path]):
    print("\n[1] Unused import detection (heuristic)")
    any_flagged = False
    for path in files:
        raw   = _read(path)
        clean = _strip_noise(raw)
        rel   = _rel(path)

        # Collect import lines and the names they bring in scope
        suspicious: list[str] = []
        for m in _IMPORT_LINE.finditer(raw):
            names_str = m.group(1)
            for part in names_str.split(","):
                part = part.strip()
                am   = _IMPORT_AS.match(part)
                if not am:
                    continue
                original = am.group(1).strip()
                alias    = (am.group(2) or original).strip()

                if not alias or alias in ("*",):
                    continue   # star imports — skip

                # Remove the import statement itself so we only
                # count uses in the rest of the file
                body_without_import = clean.replace(m.group(0), "", 1)
                # Count word-boundary occurrences of the alias
                uses = len(re.findall(rf"\b{re.escape(alias)}\b", body_without_import))
                if uses == 0:
                    suspicious.append(alias)

        if suspicious:
            warn(f"{rel} — possibly unused imports: {', '.join(suspicious)}")
            any_flagged = True

    if not any_flagged:
        ok("No obviously unused imports detected across SCAN_DIRS")


# ═══════════════════════════════════════════════════════════════════
# CHECK 2: Orphan file detection
#
# A file is "orphaned" if its module name appears in NO other Python
# file's import statements within the codebase.
# Entry points (main, __main__, __init__, conftest) are excluded.
# ═══════════════════════════════════════════════════════════════════

def check_orphan_files(files: list[Path]):
    print("\n[2] Orphan file detection")

    # Build combined import source (all files concatenated)
    all_imports_source = "\n".join(_read(f) for f in files)

    orphans = []
    for path in files:
        if path.stem in ENTRY_POINT_STEMS:
            continue   # entry points are intentionally not imported

        module_name = path.stem   # e.g. "llm_router" from "llm_router.py"
        layer       = path.relative_to(ROOT).parts[0]   # e.g. "services"

        # Look for `from services.llm_router` or `from .llm_router` or `import llm_router`
        patterns = [
            rf"from\s+{re.escape(layer)}\.{re.escape(module_name)}",
            rf"from\s+\.{re.escape(module_name)}",
            rf"import\s+{re.escape(module_name)}\b",
        ]
        imported = any(re.search(p, all_imports_source) for p in patterns)
        if not imported:
            orphans.append(_rel(path))

    if orphans:
        for o in orphans:
            warn(f"Potential orphan — not imported anywhere: {o}")
    else:
        ok("No orphan files detected")


# ═══════════════════════════════════════════════════════════════════
# CHECK 3: Deprecated / legacy module markers
# ═══════════════════════════════════════════════════════════════════

def check_deprecated_markers(files: list[Path]):
    print("\n[3] Deprecated / legacy markers")
    found_any = False
    for path in files:
        content = _read(path)
        hits    = []
        for marker in DEPRECATED_MARKERS:
            if marker.lower() in content.lower():
                # Count occurrences
                count = content.lower().count(marker.lower())
                hits.append(f"'{marker}' ×{count}")
        if hits:
            warn(f"{_rel(path)} — contains deprecated markers: {', '.join(hits)}")
            found_any = True
    if not found_any:
        ok("No deprecated/legacy markers found")


# ═══════════════════════════════════════════════════════════════════
# CHECK 4: Empty or near-empty files
# ═══════════════════════════════════════════════════════════════════

MIN_MEANINGFUL_LINES = 5   # fewer than this → suspect placeholder

def check_empty_files(files: list[Path]):
    print("\n[4] Empty / near-empty file detection")
    empties = []
    for path in files:
        if path.stem in ENTRY_POINT_STEMS:
            continue
        content      = _read(path)
        non_blank    = [l for l in content.splitlines() if l.strip() and not l.strip().startswith("#")]
        if len(non_blank) < MIN_MEANINGFUL_LINES:
            empties.append(f"{_rel(path)} ({len(non_blank)} non-blank non-comment lines)")
    if empties:
        for e in empties:
            warn(f"Near-empty file — consider populating or removing: {e}")
    else:
        ok("All files contain sufficient content")


# ═══════════════════════════════════════════════════════════════════
# CHECK 5: TODO / FIXME density
# ═══════════════════════════════════════════════════════════════════

_TODO_PATTERN = re.compile(r"\b(TODO|FIXME|HACK|NOQA)\b", re.IGNORECASE)

def check_todo_density(files: list[Path]):
    print("\n[5] TODO / FIXME density check")
    dense_files = []
    for path in files:
        content    = _read(path)
        lines      = content.splitlines()
        total      = len(lines) or 1
        todo_count = sum(1 for l in lines if _TODO_PATTERN.search(l))
        density    = todo_count / total
        if density > MAX_TODO_DENSITY and todo_count > 3:
            dense_files.append(
                f"{_rel(path)} — {todo_count} TODO/FIXME in {total} lines "
                f"({round(density * 100)}% density)"
            )
    if dense_files:
        for d in dense_files:
            warn(f"High TODO density: {d}")
    else:
        ok(f"TODO density within acceptable threshold (< {int(MAX_TODO_DENSITY * 100)}%)")


# ═══════════════════════════════════════════════════════════════════
# CHECK 6: Hard enforcement — no file in services/ should contain
#          direct DB session creation (use SessionDep / engine only)
#          This catches services that bypass the session contract.
# ═══════════════════════════════════════════════════════════════════

_RAW_SESSION_PATTERN = re.compile(r"\bSessionLocal\s*\(\s*\)|\bsessionmaker\s*\(", re.MULTILINE)

def check_service_session_contract(files: list[Path]):
    print("\n[6] Service session contract (no raw SessionLocal/sessionmaker in services/)")
    violations = 0
    for path in files:
        if path.relative_to(ROOT).parts[0] != "services":
            continue
        content = _read(path)
        if _RAW_SESSION_PATTERN.search(content):
            fail(
                f"services/{path.name} — creates a raw DB session directly. "
                "Services must accept `db: Session` as a parameter (injected by the caller)."
            )
            violations += 1
    if violations == 0:
        ok("All service files use injected Session (no raw SessionLocal)")


# ═══════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 60)
    print("  AutoRepro — Cleanup & Code Hygiene Check")
    print("=" * 60)
    print(f"  Scanning: {', '.join(SCAN_DIRS)}")

    files = _scan_files()
    print(f"  Files found: {len(files)}")

    check_unused_imports(files)
    check_orphan_files(files)
    check_deprecated_markers(files)
    check_empty_files(files)
    check_todo_density(files)
    check_service_session_contract(files)

    print("\n" + "=" * 60)

    if WARNINGS:
        print(f"\nAdvisory Warnings ({len(WARNINGS)}) — review but do not block:")
        for w in WARNINGS:
            print(w)

    if ERRORS:
        print(f"\nFailed: {len(ERRORS)} hard violation(s) — must be resolved:")
        for e in ERRORS:
            print(e)
        print()
        print("  Fix the above violations before marking the phase as complete.")
        sys.exit(1)
    else:
        print(f"\n[CLEANUP CHECK PASSED]")
        if WARNINGS:
            print(f"  {len(WARNINGS)} advisory warning(s) above.")
            print("  These are informational. Resolve when convenient.")
        sys.exit(0)

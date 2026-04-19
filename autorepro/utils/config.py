"""Central configuration — all settings read from .env file and environment variables."""

import json
import os
from dotenv import load_dotenv

# Load .env file (does NOT override existing env vars, so CLI still works as override)
load_dotenv()

LLM_PROVIDER: str            = os.getenv("LLM_PROVIDER", "ollama")
LLM_MODEL: str               = os.getenv("LLM_MODEL", "qwen2.5-coder:3b")
MAX_ATTEMPTS: int            = int(os.getenv("MAX_ATTEMPTS", "5"))
SANDBOX_TIMEOUT_SECONDS: int = int(os.getenv("SANDBOX_TIMEOUT_SECONDS", "60"))
SANDBOX_MEMORY_MB: int       = int(os.getenv("SANDBOX_MEMORY_MB", "512"))
SANDBOX_IMAGE: str           = os.getenv("SANDBOX_IMAGE", "autorepro-sandbox:latest")
DATA_DIR: str                = os.getenv("DATA_DIR", "./data")
LOG_LEVEL: str               = os.getenv("LOG_LEVEL", "INFO")
DEMO_MODE: bool              = os.getenv("DEMO_MODE", "").lower() in ("1", "true", "yes")

# Global AutoRepro execution switch.
# Set ENABLE_AUTOREPRO=false in .env to disable job triggering entirely
# (e.g. during maintenance, cost freeze, or emergency stop).
# All other API operations remain functional.
ENABLE_AUTOREPRO: bool = os.getenv("ENABLE_AUTOREPRO", "true").lower() not in ("0", "false", "no")

# ── Database / Celery / Auth (Phase 1+) ──────────────────────────
DATABASE_URL: str            = os.getenv("DATABASE_URL", "postgresql://autorepro:autorepro@localhost:5432/autorepro")
REDIS_URL: str               = os.getenv("REDIS_URL", "redis://localhost:6379/0")
SECRET_KEY: str              = os.getenv("SECRET_KEY", "change-me-in-production")

# ── Phase 2: LLM Fallback, JWT, CORS ────────────────────────────
PRIMARY_LLM_PROVIDER: str    = os.getenv("PRIMARY_LLM_PROVIDER", LLM_PROVIDER)
PRIMARY_LLM_MODEL: str       = os.getenv("PRIMARY_LLM_MODEL", LLM_MODEL)
FALLBACK_LLM_PROVIDER: str   = os.getenv("FALLBACK_LLM_PROVIDER", "google")
FALLBACK_LLM_MODEL: str      = os.getenv("FALLBACK_LLM_MODEL", "gemini-2.0-flash")
ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60"))

# ── Phase 4: Concurrency limits ──────────────────────────────────
# MAX_COMPANY_CONCURRENT_JOBS: how many active jobs the entire company can run at once.
# Increase for high-volume teams; keep low during cost-freeze periods.
MAX_COMPANY_CONCURRENT_JOBS: int = int(os.getenv("MAX_COMPANY_CONCURRENT_JOBS", "10"))

# MAX_USER_CONCURRENT_JOBS: how many active jobs a SINGLE user can run simultaneously.
MAX_USER_CONCURRENT_JOBS: int = int(os.getenv("MAX_USER_CONCURRENT_JOBS", "5"))

# MAX_RUNS_PER_USER_PER_DAY: daily trigger rate limit per user (resets at midnight UTC).
MAX_RUNS_PER_USER_PER_DAY: int = int(os.getenv("MAX_RUNS_PER_USER_PER_DAY", "20"))

# ALLOWED_ORIGINS: JSON array string or comma-separated list
_origins_raw = os.getenv("ALLOWED_ORIGINS", '["http://localhost:3000","http://localhost:8000"]')
try:
    ALLOWED_ORIGINS: list[str] = json.loads(_origins_raw)
except (json.JSONDecodeError, TypeError):
    ALLOWED_ORIGINS: list[str] = [o.strip() for o in _origins_raw.split(",") if o.strip()]


"""
AutoRepro Enterprise — Central Configuration (V2.0)

All settings are read from .env file and environment variables.
Every module imports from here — never read os.getenv() directly elsewhere.

Design decisions:
  - load_dotenv() does NOT override existing env vars, so CLI overrides still work.
  - Defaults are set for local development (Postgres, Mailhog, localhost Redis).
  - Gemini free-tier limits are enforced via GEMINI_RPM_LIMIT and GEMINI_DAILY_TOKEN_LIMIT.
  - CORS origins support both JSON array and comma-separated formats.
"""

import json
import os
from dotenv import load_dotenv

# Load .env file (does NOT override existing env vars, so CLI still works as override)
load_dotenv()


# ═══════════════════════════════════════════════════════════════════
# DATABASE
# ═══════════════════════════════════════════════════════════════════
DATABASE_URL: str = os.getenv(
    "DATABASE_URL",
    "postgresql://autorepro:autorepro@localhost:5432/autorepro",
)

# ═══════════════════════════════════════════════════════════════════
# REDIS
# ═══════════════════════════════════════════════════════════════════
REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# ═══════════════════════════════════════════════════════════════════
# LLM PROVIDERS
# ═══════════════════════════════════════════════════════════════════
LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "gemini")           # gemini, groq, ollama
LLM_MODEL: str    = os.getenv("LLM_MODEL", "gemini-1.5-flash")

# Fallback (used when primary quota is exhausted)
PRIMARY_LLM_PROVIDER: str  = os.getenv("PRIMARY_LLM_PROVIDER", LLM_PROVIDER)
PRIMARY_LLM_MODEL: str     = os.getenv("PRIMARY_LLM_MODEL", LLM_MODEL)
FALLBACK_LLM_PROVIDER: str = os.getenv("FALLBACK_LLM_PROVIDER", "google")
FALLBACK_LLM_MODEL: str    = os.getenv("FALLBACK_LLM_MODEL", "gemini-2.0-flash")

# ═══════════════════════════════════════════════════════════════════
# GEMINI FREE TIER LIMITS
# ═══════════════════════════════════════════════════════════════════
GEMINI_RPM_LIMIT: int          = int(os.getenv("GEMINI_RPM_LIMIT", "15"))           # requests per minute
GEMINI_DAILY_TOKEN_LIMIT: int  = int(os.getenv("GEMINI_DAILY_TOKEN_LIMIT", "1500000"))  # 1.5M tokens/day

# ═══════════════════════════════════════════════════════════════════
# AGENT EXECUTION
# ═══════════════════════════════════════════════════════════════════
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

# ═══════════════════════════════════════════════════════════════════
# EMAIL (SMTP)
# ═══════════════════════════════════════════════════════════════════
SMTP_HOST: str     = os.getenv("SMTP_HOST", "localhost")
SMTP_PORT: int     = int(os.getenv("SMTP_PORT", "1025"))       # Mailhog for dev
SMTP_USER: str     = os.getenv("SMTP_USER", "")
SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD", "")
FROM_EMAIL: str    = os.getenv("FROM_EMAIL", "noreply@autorepro.dev")

# ═══════════════════════════════════════════════════════════════════
# FILE STORAGE (local filesystem)
# ═══════════════════════════════════════════════════════════════════
UPLOAD_DIR: str            = os.getenv("UPLOAD_DIR", "./data/attachments")
MAX_ATTACHMENT_SIZE_MB: int = int(os.getenv("MAX_ATTACHMENT_SIZE_MB", "10"))

# ═══════════════════════════════════════════════════════════════════
# DATA RETENTION
# ═══════════════════════════════════════════════════════════════════
RETENTION_DAYS_JOBS: int        = int(os.getenv("RETENTION_DAYS_JOBS", "90"))
RETENTION_DAYS_ATTACHMENTS: int = int(os.getenv("RETENTION_DAYS_ATTACHMENTS", "365"))

# ═══════════════════════════════════════════════════════════════════
# RATE LIMITING & CONCURRENCY
# ═══════════════════════════════════════════════════════════════════
API_RATE_LIMIT_PER_MINUTE: int   = int(os.getenv("API_RATE_LIMIT_PER_MINUTE", "60"))
MAX_RUNS_PER_USER_PER_DAY: int   = int(os.getenv("MAX_RUNS_PER_USER_PER_DAY", "20"))
MAX_COMPANY_CONCURRENT_JOBS: int = int(os.getenv("MAX_COMPANY_CONCURRENT_JOBS", "10"))
MAX_USER_CONCURRENT_JOBS: int    = int(os.getenv("MAX_USER_CONCURRENT_JOBS", "5"))

# ═══════════════════════════════════════════════════════════════════
# REAL-TIME (WebSocket / SSE)
# ═══════════════════════════════════════════════════════════════════
ENABLE_WEBSOCKETS: bool     = os.getenv("ENABLE_WEBSOCKETS", "true").lower() not in ("0", "false", "no")
WEBSOCKET_PING_INTERVAL: int = int(os.getenv("WEBSOCKET_PING_INTERVAL", "30"))

# ═══════════════════════════════════════════════════════════════════
# CORS
# ═══════════════════════════════════════════════════════════════════
_origins_raw = os.getenv("ALLOWED_ORIGINS", '["http://localhost:3000","http://localhost:5173","http://localhost:8000"]')
try:
    ALLOWED_ORIGINS: list[str] = json.loads(_origins_raw)
except (json.JSONDecodeError, TypeError):
    ALLOWED_ORIGINS: list[str] = [o.strip() for o in _origins_raw.split(",") if o.strip()]

# Alias used by some modules
CORS_ORIGINS = ALLOWED_ORIGINS

# ═══════════════════════════════════════════════════════════════════
# JWT / AUTH
# ═══════════════════════════════════════════════════════════════════
SECRET_KEY: str                  = os.getenv("SECRET_KEY", "change-me-in-production")
JWT_SECRET: str                  = os.getenv("JWT_SECRET", SECRET_KEY)
JWT_ALGORITHM: str               = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "43200"))  # 30 days

# ═══════════════════════════════════════════════════════════════════
# SENTRY (optional — leave empty to disable)
# ═══════════════════════════════════════════════════════════════════
SENTRY_DSN: str = os.getenv("SENTRY_DSN", "")

# ═══════════════════════════════════════════════════════════════════
# ENVIRONMENT
# ═══════════════════════════════════════════════════════════════════
ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")  # development, staging, production
"""Central configuration complete.  All modules import from here."""

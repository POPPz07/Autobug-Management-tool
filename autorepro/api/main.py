"""
AutoRepro Enterprise — FastAPI Application Entry Point

Router registration order:
  /api/v1/auth/...      - Authentication & user management (Phase 1.5)
  /api/v1/bugs/...      - Bug CRUD + lifecycle + assignment (Phase 2)
  /api/v1/teams/...     - Team management (Phase 2)
  /api/v1/jobs/...      - Execution trigger + status (Phase 2)
  /api/v1/bugs/.../comments - Threaded comments (Phase 2)

Legacy routes (original AutoRepro engine) are preserved unchanged:
  /reproduce, /result/{job_id}, /jobs  — from api/routes.py
"""

import docker
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlmodel import SQLModel
from starlette.middleware.base import BaseHTTPMiddleware

# Routers
from api.routes         import router           # legacy: /reproduce, /result, /jobs
from api.auth           import auth_router      # /api/v1/auth/*
from api.bug_routes     import bug_router       # /api/v1/bugs/*
from api.team_routes    import teams_router     # /api/v1/teams/*
from api.job_routes     import job_router       # /api/v1/jobs/*
from api.comment_routes import comments_router  # /api/v1/bugs/{id}/comments
# V2.0 routers
from api.platform_routes     import router as platform_router    # /api/v1/platform/*
from api.notification_routes import router as notification_router # /api/v1/notifications/*
from api.template_routes     import router as template_router    # /api/v1/templates/*
from api.label_routes        import router as label_router       # /api/v1/labels/*
from api.webhook_routes      import router as webhook_router     # /api/v1/webhooks/*
from api.bulk_routes         import router as bulk_router        # /api/v1/bugs/bulk/*
from api.websocket_routes    import router as ws_router          # /ws
from api.sse_routes          import router as sse_router         # /api/v1/events/*
from api.api_key_routes      import router as api_key_router     # /api/v1/api-keys/*

from db.session import engine
from db import models     # noqa: F401 — registers all SQLModel table metadata
from db import models_v2  # noqa: F401 — registers V2.0 model tables
from utils import config
from utils.logger import get_logger

log = get_logger(__name__)


# ═══════════════════════════════════════════════════════════════════
# BREACH PREVENTION MIDDLEWARE
# Strips Accept-Encoding on auth endpoints to prevent BREACH attacks.
# Must run BEFORE GZip so it can remove the header first.
# ═══════════════════════════════════════════════════════════════════

class NoCompressAuthMiddleware(BaseHTTPMiddleware):
    EXCLUDED_PREFIXES = ("/api/v1/auth/",)

    async def dispatch(self, request: Request, call_next):
        for prefix in self.EXCLUDED_PREFIXES:
            if request.url.path.startswith(prefix):
                headers = dict(request.scope["headers"])
                headers.pop(b"accept-encoding", None)
                request.scope["headers"] = list(headers.items())
                break
        return await call_next(request)


# ═══════════════════════════════════════════════════════════════════
# LIFESPAN
# ═══════════════════════════════════════════════════════════════════

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: create DB tables, verify Docker, ensure data dirs, start WebSocket broadcaster."""
    # Create all tables (existing + V2.0 new models)
    SQLModel.metadata.create_all(engine)
    log.info("db_tables_ready")

    try:
        docker.from_env().ping()
        log.info("docker_daemon_ok")
    except Exception as exc:
        log.warning("docker_daemon_unavailable", error=str(exc))

    Path(config.DATA_DIR, "jobs").mkdir(parents=True, exist_ok=True)
    Path(config.DATA_DIR, "artifacts").mkdir(parents=True, exist_ok=True)
    Path(config.UPLOAD_DIR).mkdir(parents=True, exist_ok=True)

    # Start Redis broadcaster for WebSocket real-time events
    if config.ENABLE_WEBSOCKETS:
        import asyncio
        from realtime.broadcaster import broadcaster
        asyncio.create_task(broadcaster.start())
        log.info("realtime_broadcaster_started")

    yield


# ═══════════════════════════════════════════════════════════════════
# APP SETUP
# ═══════════════════════════════════════════════════════════════════

app = FastAPI(
    title       = "AutoRepro Enterprise",
    version     = "2.0.0",
    description = "AI-powered Bug Operations SaaS Platform",
    lifespan    = lifespan,
)


# ── Global error handler for standardized error envelope ─────────

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """
    Catch-all: any unhandled exception returns a standardized error envelope.
    HTTPExceptions with a dict detail (our err() helpers) are passed through.
    """
    from fastapi import HTTPException
    if isinstance(exc, HTTPException):
        detail = exc.detail
        # If already a standardized envelope, pass through
        if isinstance(detail, dict) and "error" in detail:
            return JSONResponse(status_code=exc.status_code, content=detail)
        # Otherwise, wrap it
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": "HTTP_ERROR", "message": str(detail)}},
        )
    log.error("unhandled_exception", path=str(request.url), error=str(exc), exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"error": {"code": "INTERNAL_ERROR", "message": "An unexpected error occurred"}},
    )


# ── Middleware stack (order matters) ──────────────────────────────

# 1. BREACH prevention (must be outermost / first)
app.add_middleware(NoCompressAuthMiddleware)

# 2. GZip for large payloads
app.add_middleware(GZipMiddleware, minimum_size=500)

# 3. CORS — never wildcard with credentials
app.add_middleware(
    CORSMiddleware,
    allow_origins     = config.ALLOWED_ORIGINS,
    allow_credentials = True,
    allow_methods     = ["*"],
    allow_headers     = ["*"],
)


# ── Router registration ───────────────────────────────────────────

# Legacy engine endpoints — MUST NOT be modified (MASTER_PLAN rule)
app.include_router(router)

# Phase 1.5 — Auth & RBAC
app.include_router(auth_router)

# Phase 2 — Enterprise API (existing)
app.include_router(bug_router)
app.include_router(teams_router)
app.include_router(job_router)
app.include_router(comments_router)

# V2.0 — New routes (Phases 2-10)
app.include_router(platform_router)     # /api/v1/platform/*
app.include_router(notification_router) # /api/v1/notifications/*
app.include_router(template_router)     # /api/v1/templates/*
app.include_router(label_router)        # /api/v1/labels/*
app.include_router(webhook_router)      # /api/v1/webhooks/*
app.include_router(bulk_router)         # /api/v1/bugs/bulk/*
app.include_router(ws_router)           # /ws (WebSocket)
app.include_router(sse_router)          # /api/v1/events/*
app.include_router(api_key_router)      # /api/v1/api-keys/*


# ── Static assets (only when built frontend is present) ───────────

static_dir = Path(__file__).resolve().parent.parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/", include_in_schema=False)
    async def serve_spa():
        return FileResponse(str(static_dir / "index.html"))


# ── Health check ──────────────────────────────────────────────────

@app.get("/health", tags=["system"])
async def health():
    return {"status": "ok", "version": "2.0.0"}

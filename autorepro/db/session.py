"""Database engine and session dependency for FastAPI / Celery.

Connection pooling is configured for shared usage across FastAPI request
handlers *and* Celery worker processes to prevent connection exhaustion.
SQLite is supported for local development (no pooling args needed).
"""

from typing import Annotated, Generator

from fastapi import Depends
from sqlmodel import Session, create_engine

from utils.config import DATABASE_URL

# ── Engine ────────────────────────────────────────────────────────
# SQLite doesn't support pool_size / max_overflow — detect which DB is in use.
if DATABASE_URL.startswith("sqlite"):
    # SQLite: single-file local dev database, no pooling config needed.
    # connect_args check_same_thread=False is required for FastAPI's async use.
    engine = create_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False},
        echo=False,
    )
else:
    # PostgreSQL (production): connection pooling for concurrent load.
    # pool_size  = 20  — steady-state connections kept open
    # max_overflow = 10 — extra connections allowed under load (total cap: 30)
    engine = create_engine(
        DATABASE_URL,
        pool_size=20,
        max_overflow=10,
        pool_pre_ping=True,   # recycle stale connections transparently
        echo=False,
    )


# ── FastAPI dependency ────────────────────────────────────────────
def get_session() -> Generator[Session, None, None]:
    """Yield a new SQLModel session per request, auto-closing on exit."""
    with Session(engine) as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]

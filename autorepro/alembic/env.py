"""Alembic env.py — dynamic configuration for AutoRepro.

Reads DATABASE_URL from environment (via utils.config) so we never
hardcode credentials in alembic.ini.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# ── Import SQLModel + all models so metadata is populated ─────────
from sqlmodel import SQLModel

from db.models import (  # noqa: F401 — imported for side-effect (table registration)
    User,
    Team,
    Bug,
    BugRun,
    Artifact,
    Comment,
)
from utils.config import DATABASE_URL

# ── Alembic Config object ────────────────────────────────────────
config = context.config

# Override the sqlalchemy.url from alembic.ini with our env-based URL
config.set_main_option("sqlalchemy.url", DATABASE_URL)

# Interpret the config file for Python logging
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Tell Alembic about our SQLModel metadata (all table=True models)
target_metadata = SQLModel.metadata


# ── Offline migrations (SQL script generation) ───────────────────
def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode — emits SQL to stdout."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


# ── Online migrations (live DB connection) ───────────────────────
def run_migrations_online() -> None:
    """Run migrations in 'online' mode — connects to the database."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

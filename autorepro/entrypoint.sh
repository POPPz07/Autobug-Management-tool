#!/bin/bash
# ------------------------------------------------------------------
# entrypoint.sh — Wait for PostgreSQL before starting the service
# Used by both `web` and `celery_worker` containers.
# ------------------------------------------------------------------

set -e

HOST="${POSTGRES_HOST:-db}"
PORT="${POSTGRES_PORT:-5432}"

echo "⏳ Waiting for PostgreSQL at ${HOST}:${PORT}..."

until pg_isready -h "$HOST" -p "$PORT" -q; do
  echo "   PostgreSQL not ready — retrying in 2s..."
  sleep 2
done

echo "✅ PostgreSQL is ready — starting service."

# Hand off to the CMD supplied by docker-compose
exec "$@"

# =============================================================
# AutoRepro — API Container
# Python 3.11 slim base, matching the sandbox image version.
# Build context: repo root (autorepro/ subdirectory is the app).
# =============================================================

FROM python:3.11-slim

# Install system dependencies:
#   - curl: health checks in docker-compose
#   - postgresql-client: pg_isready in entrypoint.sh
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl postgresql-client \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (cached layer — only rebuilds on requirements change)
COPY autorepro/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY autorepro/ .

# Copy and enable the Postgres-readiness entrypoint script
RUN chmod +x /app/entrypoint.sh

# Create data directories (overridden by volume mount in production)
RUN mkdir -p data/jobs data/artifacts

EXPOSE 8000

# Entrypoint waits for Postgres, then hands off to CMD
ENTRYPOINT ["/app/entrypoint.sh"]

# Default command — overridden by docker-compose per service
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]

# ============================================================
# Healthcare Bot - Production Dockerfile
# ============================================================
# Multi-stage build for a lean, secure production image.
# ============================================================

# --------------- Stage 1: Builder ---------------
FROM python:3.11-slim AS builder

# Install build-time system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements first to leverage Docker layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# --------------- Stage 2: Runtime ---------------
FROM python:3.11-slim AS runtime

# Install only the runtime libraries (no compiler)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy installed Python packages from builder
COPY --from=builder /install /usr/local

WORKDIR /app

# Copy application source
COPY . .

# Create a non-root user for security
RUN groupadd --gid 1001 appuser \
    && useradd --uid 1001 --gid appuser --shell /bin/bash --create-home appuser \
    && chown -R appuser:appuser /app

USER appuser

# Expose the application port
EXPOSE 8000

# Healthcheck — hit the /health endpoint every 30s
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Start with Gunicorn + Uvicorn workers
CMD ["gunicorn", "app.main:app", "-c", "gunicorn.conf.py"]

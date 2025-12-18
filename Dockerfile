# ============================================================================
# Multi-stage Dockerfile for Lyo SaaS Production
# Optimized for security, performance, and size
# ============================================================================

# Build stage - Install dependencies
FROM python:3.11-slim as builder

# Set build arguments
ARG BUILD_DATE
ARG GIT_COMMIT
ARG VERSION=1.0.0

# Install system dependencies for building
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt requirements.webhook.txt ./

# Install Python dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir -r requirements.webhook.txt

# Production stage - Runtime environment
FROM python:3.11-slim as production

# Set metadata labels
LABEL maintainer="Lyo SaaS Team" \
      version="${VERSION}" \
      description="Lyo Italian Booking Assistant SaaS" \
      build-date="${BUILD_DATE}" \
      git-commit="${GIT_COMMIT}"

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PATH="/app/venv/bin:$PATH" \
    ENVIRONMENT=production \
    HOST=0.0.0.0 \
    PORT=8000 \
    WORKERS=2

# Install runtime system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get autoremove -y \
    && apt-get clean

# Create non-root user for security
RUN groupadd -r lyo && useradd -r -g lyo lyo

# Create application directory
WORKDIR /app

# Copy Python packages from builder
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application code
COPY . .

# Set proper ownership
RUN chown -R lyo:lyo /app

# Switch to non-root user
USER lyo

# Create directories for logs and temp files
RUN mkdir -p /app/logs /app/tmp

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Expose port
EXPOSE 8000

# Default command
CMD ["python", "-m", "gunicorn", "app.main:app", \
     "--worker-class", "uvicorn.workers.UvicornWorker", \
     "--workers", "${WORKERS}", \
     "--bind", "${HOST}:${PORT}", \
     "--timeout", "120", \
     "--keepalive", "5", \
     "--max-requests", "1000", \
     "--max-requests-jitter", "100", \
     "--preload", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "--log-level", "info"]
# Build stage
FROM python:3.11-slim as builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libhts-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --user --no-cache-dir -r requirements.txt

# Runtime stage
FROM python:3.11-slim

WORKDIR /app

# Install runtime dependencies only (no build tools)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libhts3 \
    dumb-init \
    gosu \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN useradd -m -u 1001 appuser && chown -R appuser:appuser /app

# Create uploads directory with correct ownership BEFORE switching user.
# Docker named volumes inherit permissions on first mount from the image.
RUN mkdir -p /app/uploads && chown appuser:appuser /app/uploads

# Copy pre-built packages from builder and fix ownership
COPY --from=builder /root/.local /home/appuser/.local
RUN chown -R appuser:appuser /home/appuser/.local

# Copy application code
COPY --chown=appuser:appuser app ./app
COPY --chown=appuser:appuser alembic ./alembic
COPY --chown=appuser:appuser alembic.ini .

# Copy entrypoint script (runs as root to fix volume permissions, then drops to appuser)
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Set PATH and PYTHONUSERBASE so Python finds the --user installed packages
ENV PATH=/home/appuser/.local/bin:$PATH
ENV PYTHONUSERBASE=/home/appuser/.local
ENV PYTHONUNBUFFERED=1

# Pre-download pyensembl data so 25mer extraction doesn't need network at runtime.
# This adds ~400MB to the image but avoids a 5+ minute download on first call.
RUN su appuser -c "pyensembl install --release 110 --species homo_sapiens" || \
    echo "WARNING: pyensembl data download failed. 25mer mode will be unavailable."

EXPOSE 8000

# Health check (runs as root, which is fine for a simple HTTP check)
HEALTHCHECK --interval=30s --timeout=3s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=2)"

# Use dumb-init for signal handling, then entrypoint fixes volume
# permissions and drops to appuser via gosu.
ENTRYPOINT ["dumb-init", "--", "/entrypoint.sh"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

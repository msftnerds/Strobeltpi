# syntax=docker/dockerfile:1.7
# Multi-arch capable base (supports linux/arm64 for Raspberry Pi 5 and linux/amd64 for local dev)
FROM python:3.11-slim AS runtime

# Metadata
LABEL org.opencontainers.image.title="strobeltpi-docker-metrics" \
      org.opencontainers.image.description="Raspberry Pi Docker container metrics exporter to Azure Event Hub" \
      org.opencontainers.image.source="https://example.invalid/repo" \
      org.opencontainers.image.licenses="MIT"

# Environment hardening / performance
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    # (Optional) default scrape interval override-able at run
    SCRAPE_INTERVAL_SECONDS=15

# Install only minimal system packages (certs + net utilities for DNS/TLS). Avoid build tools to keep small image.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && update-ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy project metadata first for better layer caching when only code changes
COPY pyproject.toml README.md ./
COPY src ./src

# Install package & runtime dependencies (prod only; dev extras omitted)
RUN pip install --upgrade --no-cache-dir pip setuptools wheel \
    && pip install --no-cache-dir . \
    && pip check

# Create non-root user (least privilege)
RUN useradd --create-home --shell /usr/sbin/nologin appuser \
    && chown -R appuser:appuser /app
USER appuser

# Expose no ports (push model). Healthcheck ensures process alive.
# Healthcheck: avoid relying on pgrep (procps) which is not in slim image.
# Succeeds if PID 1 command line contains our module reference.
HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=3 \
    CMD python -c "import sys; import pathlib; p=pathlib.Path('/proc/1/cmdline');\n" \
                            "data=p.read_bytes().decode(errors='ignore') if p.exists() else '';" \
                            "sys.exit(0 if 'strobeltpi.metrics_agent' in data else 1)" || exit 1

# Required runtime environment variables (must be provided at run):
#   KEYVAULT_URL, AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET
# Optionally: SCRAPE_INTERVAL_SECONDS, LOG_LEVEL

ENTRYPOINT ["python", "-m", "strobeltpi.metrics_agent"]

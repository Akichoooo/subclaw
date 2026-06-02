# subclaw - Multi-Model LLM Gateway
# https://github.com/Akichoooo/subclaw
#
# A self-hosted FastAPI proxy + slash-command orchestration framework that
# reduces Claude API spend by 60-90% on read-heavy workloads by fanning heavy work to cheap
# worker models, with session-pinned prompt-cache locality and a USD
# budget circuit breaker.

FROM python:3.12-slim AS base

# Prevent Python from writing .pyc files and buffering stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Create non-root user for runtime safety
RUN groupadd --system --gid 1001 subclaw \
    && useradd --system --uid 1001 --gid subclaw --home-dir /app --shell /sbin/nologin subclaw

WORKDIR /app

# Install dependencies first for better layer caching
COPY proxy/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy the proxy code
COPY proxy/ /app/proxy/

# Drop privileges
USER subclaw

# Default port matches PROXY_PORT in app.py
EXPOSE 4748

# Health check uses the /health endpoint exposed by FastAPI
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:4748/health').read()" || exit 1

# Run uvicorn directly for production-grade startup
CMD ["uvicorn", "proxy.app:app", "--host", "0.0.0.0", "--port", "4748", "--workers", "1"]

# === Build-time metadata ===
LABEL org.opencontainers.image.title="subclaw" \
      org.opencontainers.image.description="Multi-model LLM gateway for Claude Code. Cuts Claude API spend by 60-90% on read-heavy workloads via session-pinned multi-key rotation, protocol translation, and budget circuit breaker." \
      org.opencontainers.image.url="https://github.com/Akichoooo/subclaw" \
      org.opencontainers.image.source="https://github.com/Akichoooo/subclaw" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.authors="Akichoooo" \
      org.opencontainers.image.vendor="Akichoooo" \
      org.opencontainers.image.keywords="claude-code,llm-gateway,multi-model,prompt-cache,api-proxy,anthropic,openai,cost-reduction"

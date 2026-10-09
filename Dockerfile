# Production image for the XMPP AI agent.
FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Build tooling is needed for Slixmpp's Rust extension.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies first for better layer caching.
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --upgrade pip \
    && pip install .

# Runtime data (mount a volume to persist it).
RUN mkdir -p /app/data /app/config
VOLUME ["/app/data"]

# Run as a non-root user.
RUN useradd --create-home --uid 1000 agent \
    && chown -R agent:agent /app
USER agent

ENTRYPOINT ["python", "-m", "agent"]
CMD ["--config", "config/agent.toml"]

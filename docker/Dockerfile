FROM python:3.11-slim AS base

LABEL maintainer="VT Protocol <hello@vtprotocol.dev>"
LABEL description="VT Protocol — AI agent governance server"

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first (layer caching)
COPY pyproject.toml ./
RUN pip install --no-cache-dir -e ".[dev]" 2>/dev/null || pip install --no-cache-dir .

# Copy application code
COPY src/ src/
COPY CLAUDE.md ./

# Create .smm directory structure
RUN mkdir -p .smm/decisions .smm/cache .smm/generated .smm/audit

# Expose ports: 7842 = dashboard, 8080 = MCP HTTP
EXPOSE 7842 8080

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:7842/api/health || exit 1

# Default: run dashboard + MCP server
CMD ["python", "-m", "uvicorn", "vt_protocol.dashboard.app:app", \
     "--host", "0.0.0.0", "--port", "7842"]

# FinanceBrain API: FastAPI + agents + MCP server + redaction/ingestion CLIs.
# Build context is the repo root (see .dockerignore: secrets, key files and caches never enter it).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    PYTHONPATH=/app \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH

RUN pip install --no-cache-dir uv==0.12.13

WORKDIR /app

# Dependencies first (cached layer): includes the en_core_web_md wheel pinned in uv.lock.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project && rm -rf /root/.cache

# Application code and the committed synthetic corpus + extraction/embedding cache.
COPY backend ./backend
COPY datagen ./datagen
COPY evals ./evals
COPY data ./data

RUN useradd --create-home --uid 1000 fb \
 && mkdir -p /app/logs /app/secrets \
 && chown -R fb:fb /app
USER fb

EXPOSE 8000
# Single worker: the MCP streamable-HTTP session manager keeps per-process session state.
CMD ["uvicorn", "backend.api.app:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*", "--timeout-keep-alive", "75"]

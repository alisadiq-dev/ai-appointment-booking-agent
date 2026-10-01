FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.8.9 /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_NO_DEV=1
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-install-project

FROM python:3.12-slim
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
RUN useradd --create-home --uid 10001 appuser
COPY --from=builder --chown=appuser /app/.venv /app/.venv
COPY --chown=appuser app ./app
USER appuser
# Render injects $PORT (default 10000); default to 8080 for local runs.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]

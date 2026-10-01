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
# The host may inject $PORT; default to 8080 (SnapDeploy is configured for 8080).
EXPOSE 8080
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]

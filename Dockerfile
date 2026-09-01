# syntax=docker/dockerfile:1
FROM python:3.13-slim AS base
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:0.9.7 /uv /usr/local/bin/uv

FROM base AS deps
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

FROM base AS runtime
RUN useradd --create-home --uid 10001 app
COPY --from=deps /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
COPY src/ ./src/
COPY pyproject.toml uv.lock ./
RUN uv pip install --no-deps -e . && chown -R app:app /app
USER app
EXPOSE 8000
CMD ["uvicorn", "eventplatform.api.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]

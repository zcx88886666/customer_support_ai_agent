FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.12.20 /uv /uvx /bin/
WORKDIR /app
COPY pyproject.toml uv.lock /app/
COPY apps /app/apps
COPY packages /app/packages
COPY prompts /app/prompts
COPY services /app/services
COPY infra/migrations /app/infra/migrations
COPY alembic.ini /app/alembic.ini
RUN uv sync --locked --no-dev --extra observability
ENV PATH=/app/.venv/bin:$PATH
ENV PYTHONPATH=/app/apps/api:/app/packages:/app
CMD ["sh", "-c", "alembic upgrade head && uvicorn resolveai.api:app --host 0.0.0.0 --port 8000"]

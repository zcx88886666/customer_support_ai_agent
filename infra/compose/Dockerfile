FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.12.20 /uv /uvx /bin/
WORKDIR /app
COPY pyproject.toml uv.lock /app/
RUN uv sync --locked --no-dev --extra observability --no-install-project
COPY apps /app/apps
COPY data /app/data
COPY evals /app/evals
COPY packages /app/packages
COPY prompts /app/prompts
COPY services /app/services
COPY scripts /app/scripts
COPY infra/migrations /app/infra/migrations
COPY alembic.ini /app/alembic.ini
RUN uv sync --locked --no-dev --extra observability
ENV PATH=/app/.venv/bin:$PATH
ENV PYTHONPATH=/app/apps/api:/app/packages:/app
CMD ["sh", "-c", "alembic upgrade head && python -m resolveai.seed --clock \"$(date -u +%Y-%m-%dT%H:%M:%S+00:00)\" && python scripts/index_policies.py && uvicorn resolveai.api:app --host 0.0.0.0 --port 8000"]

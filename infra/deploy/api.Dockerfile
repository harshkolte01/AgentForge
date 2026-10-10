FROM ghcr.io/astral-sh/uv:0.13.0 AS uv
FROM python:3.12.15-slim-bookworm
COPY --from=uv /uv /usr/local/bin/uv
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_NO_CACHE=1 UV_PYTHON_DOWNLOADS=never
WORKDIR /app/apps/api
COPY apps/api/pyproject.toml apps/api/uv.lock ./
COPY apps/api/src ./src
RUN uv sync --frozen --no-dev --no-editable \
    && groupadd --gid 10001 agentforge \
    && useradd --uid 10001 --gid 10001 --no-create-home agentforge
COPY apps/api/alembic.ini ./
COPY apps/api/alembic ./alembic
ENV PATH="/app/apps/api/.venv/bin:$PATH" RESEARCH_STORAGE_PATH=/data/artifacts
USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "agentforge.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers", "--no-access-log"]

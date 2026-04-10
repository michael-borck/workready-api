FROM python:3.13-slim

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY workready_api/ workready_api/
RUN uv sync --frozen --no-dev

# Job data baked into the image at /app/jobs (NOT under /data, which is
# bind-mounted from the host and would shadow these files at runtime)
COPY jobs/ /app/jobs/

# /data is mounted from the host for persistence (SQLite database, etc).
# SITES_DIR points to the baked-in jobs at /app/jobs so it's never empty.
ENV SITES_DIR=/app/jobs \
    WORKREADY_DB=/data/workready.db \
    USE_LLM=false

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "workready_api.app:app", "--host", "0.0.0.0", "--port", "8000"]

FROM python:3.13-slim

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY workready_api/ workready_api/
RUN uv sync --frozen --no-dev

# Job data can be mounted at /data or baked in
COPY jobs/ /data/jobs/

ENV SITES_DIR=/data/jobs \
    WORKREADY_DB=/data/workready.db \
    USE_LLM=false

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "workready_api.app:app", "--host", "0.0.0.0", "--port", "8000"]

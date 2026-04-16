# WorkReady Simulation API

Backend for the WorkReady internship simulation. Tracks student progress through 6 stages and provides resume assessment.

## Quick Start

```bash
# Install
uv sync

# Run (stub mode — no LLM needed)
SITES_DIR=/path/to/loco-ensyo uv run uvicorn workready_api.app:app --reload --port 8000

# Run with local LLM (requires Ollama)
SITES_DIR=/path/to/loco-ensyo USE_LLM=true uv run uvicorn workready_api.app:app --reload --port 8000
```

Swagger docs at `http://localhost:8000/docs`

## Endpoints

### Health
- `GET /health` — returns `{"status": "ok", "version": "0.2.0"}`

### Stage 2: Resume Submission
- `POST /api/v1/resume` — submit a resume for assessment (multipart form)

| Field | Type | Required |
|-------|------|----------|
| company_slug | string | yes |
| job_slug | string | yes |
| job_title | string | yes |
| applicant_name | string | yes |
| applicant_email | string | yes |
| cover_letter | string | no |
| source | string | no — "direct" or "seek" |
| resume | PDF file | yes |

Returns assessment with fit score, feedback, and whether to proceed to interview.

### Student Progress
- `GET /api/v1/student/{email}` — all applications for a student
- `GET /api/v1/application/{id}` — full detail of an application with stage results

## Data Model

```
students (email PK, name)
    └── applications (company, job, current_stage)
            └── stage_results (stage, status, score, feedback, attempt)
```

Stages: `job_board` → `resume` → `interview` → `placement` → `mid_placement` → `exit`

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| SITES_DIR | `../../` | Path to company site directories (for job descriptions) |
| WORKREADY_DB | `workready.db` | SQLite database path |
| USE_LLM | `false` | Use Ollama for assessment instead of stub |
| OLLAMA_BASE_URL | `http://localhost:11434` | Ollama API URL |
| OLLAMA_MODEL | `llama3.2` | Model for assessment |

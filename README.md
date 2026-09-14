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
- `GET /health` — returns `{"status": "ok", "version": "0.3.0"}`

### Stage 2: Resume Submission
- `POST /api/v1/resume` — submit a resume for assessment (multipart form)

| Field | Type | Required |
|-------|------|----------|
| company_slug | string | yes (or posting_id) |
| job_slug | string | yes (or posting_id) |
| job_title | string | optional; resolved from posting |
| applicant_name | string | no — optional self-declared display name |
| cover_letter | string | no |
| source | string | no — "direct" or "seek" |
| resume | PDF file | yes |

Returns assessment with fit score, feedback, and whether to proceed to interview.

### Student Progress
- `POST /api/v1/auth/login` with `{"code":"WR-XXXX-XXXX"}` exchanges the code for an eight-hour session. Send the returned token as `Authorization: Bearer ...` on private requests.
- `POST /api/v1/auth/logout` invalidates that session. Revoking the enrolment code invalidates all of its sessions.
- `GET /api/v1/me/state` and `GET /api/v1/me/progress` return the signed-in student's journey. Old code-bearing URLs return 410.
- `GET /api/v1/application/{id}` — full detail of an application with stage results

## Data Model

```
codes (access code, cohort, active)
    └── students (code FK, fictional handle, chosen persona name)
            ├── student_sessions (token hash, expiry)
            └── applications (company, job, current_stage)
                    └── stage_results (stage, status, score, feedback, attempt)
```

Stages: `job_board` → `resume` → `interview` → `placement` → `mid_placement` → `exit`

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| SITES_DIR | `../../` | Path to company site directories (for job descriptions) |
| WORKREADY_DB | `workready.db` | SQLite database path |
| LLM_PROVIDER | `stub` | stub, ollama, anthropic, openrouter |
| OLLAMA_BASE_URL | `http://localhost:11434` | Ollama API URL |
| LLM_MODEL | provider-dependent | Model for assessment |
| STUDENT_SESSION_HOURS | `8` | Session lifetime |
| SIMULATION_PRESET | `custom` | custom, workshop or semester; individual env values override defaults |
| WORKREADY_ATTACHMENTS_DIR | DB directory + `/attachments` | Durable filtered-PDF storage |
| RETENTION_DAYS | `120` | Inactivity threshold for operator-triggered cohort purge |

Use synthetic profiles and resumes. Contact filtering is best-effort, not guaranteed anonymity. Student messages, submissions and feedback are retained for lecturer review. Cloud providers receive filtered prompts when selected.

Tests: `uv run python -m unittest discover -s tests -v`.

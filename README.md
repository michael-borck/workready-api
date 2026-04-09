# WorkReady Resume Assessment API

Receives resume submissions from WorkReady company sites and the WorkReady Jobs board, scores them against job requirements, and returns structured feedback.

## Quick Start

```bash
# Install
uv sync

# Run (stub mode — no LLM needed)
uv run uvicorn workready_api.app:app --reload --port 8000

# Run with local LLM (requires Ollama)
USE_LLM=true uv run uvicorn workready_api.app:app --reload --port 8000
```

## API

### POST /api/v1/resume

Submit a resume for assessment. Multipart form data:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| company_slug | string | yes | Company identifier |
| job_slug | string | yes | Job posting identifier |
| job_title | string | yes | Job title |
| applicant_name | string | yes | Applicant's name |
| applicant_email | string | yes | Applicant's email |
| cover_letter | string | no | Cover letter text |
| source | string | no | "direct" or "seek" |
| resume | file | yes | PDF file |

### Response

```json
{
  "status": "reviewed",
  "fit_score": 72,
  "feedback": {
    "strengths": ["Good keyword alignment with the job description"],
    "gaps": ["No cover letter submitted"],
    "suggestions": ["Include a cover letter tailored to this specific role"],
    "tailoring": "This resume appears generic..."
  },
  "proceed_to_interview": true,
  "message": "Your application looks strong — proceed to the interview stage!"
}
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| SITES_DIR | `../../` (relative to package) | Path to directory containing company site folders |
| USE_LLM | `false` | Set to `true` to use Ollama for assessment |
| OLLAMA_BASE_URL | `http://localhost:11434` | Ollama API URL |
| OLLAMA_MODEL | `llama3.2` | Model to use for assessment |

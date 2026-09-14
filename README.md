# WorkReady API

FastAPI backend for WorkReady's educational internship simulation. It manages student sessions, applications, interviews, placement tasks, messages, lunchroom conversations, feedback and lecturer reports.

[Project home](https://github.com/michael-borck/workready-deploy) · [Architecture](https://github.com/michael-borck/workready-deploy/blob/main/docs/architecture.md) · [Configuration](https://github.com/michael-borck/workready-deploy/blob/main/docs/configuration.md) · [Privacy](https://github.com/michael-borck/workready-deploy/blob/main/docs/privacy.md)

## Run locally

Requires Python 3.11 or later and [uv](https://docs.astral.sh/uv/). From this repository:

```bash
uv sync --frozen
WORKREADY_DB=/tmp/workready-local.db SITES_DIR="$PWD/jobs" LLM_PROVIDER=stub \
  uv run uvicorn workready_api.app:app --host 127.0.0.1 --port 8000 --no-access-log
```

This uses the checked-in flat job exports and a local database. For full character prompt files, keep the company repositories as siblings and point `SITES_DIR` at their parent. Reconcile company-root `jobs.json` copies with this repository's exports first; the loader prefers the company-root layout.

The command does not implicitly load `.env`. Export settings explicitly. For Ollama, select `LLM_PROVIDER=ollama` and an available `LLM_MODEL`; `USE_LLM=true` is not a supported switch. The [configuration guide](https://github.com/michael-borck/workready-deploy/blob/main/docs/configuration.md) owns provider, timing, storage and request-limit settings.

Open `http://127.0.0.1:8000/docs` for generated OpenAPI documentation, or check `/health`. Stub responses are for development and demonstrations.

## Authentication and API contract

An operator issues contractor codes through the admin API. Unknown codes do not create students automatically.

- `POST /api/v1/auth/login` accepts `{"code":"WR-XXXX-XXXX"}` and returns an expiring session. The example code is a placeholder.
- Send the returned token as `Authorization: Bearer TOKEN` on private requests.
- `POST /api/v1/auth/logout` revokes that session. Code revocation invalidates all sessions tied to the code.
- `/api/v1/me/state`, `/api/v1/me/progress`, `/api/v1/me/profile` and `/api/v1/inbox` operate on the authenticated student. Retired code-bearing private URLs return 410.
- Application, task and conversation routes enforce ownership and relevant conversation kind.
- `/api/v1/admin/*` requires the separate `WORKREADY_ADMIN_TOKEN`. A blank token disables those endpoints.

Use the generated OpenAPI schema for complete request fields, upload-preview routes and response shapes. A resume passing review advances to interview, not directly to employment.

## Source map

| Path | Responsibility |
|---|---|
| `workready_api/app.py` | Routes and simulation transitions |
| `workready_api/auth.py` | Sessions and shared private-route protection |
| `workready_api/db.py`, `erasure.py` | SQLite schema, records and cleanup |
| `workready_api/jobs.py`, `jobs/` | Runtime export loader and canonical authoring exports |
| `workready_api/scheduling.py`, `blocking.py` | Timing, presets and repeat-application rules |
| `workready_api/pdf.py` | PDF limits, text extraction and contact filtering |
| Other `workready_api/` modules | Assessment, characters, communication and journey reports |
| `scripts/migrate_attachments.py` | Offline legacy attachment migration |
| `tests/` | Privacy regressions and isolated browser journey |
| [Design archive](docs/README.md) | Historical specifications, not the current API contract |

## Verify changes

```bash
uv run python -m unittest discover -s tests -v
uv run --with playwright python tests/browser_flow.py
```

The browser test needs all eleven sibling repositories and the company sites' built `dist/` outputs. It uses synthetic fixtures and intercepted requests. Set `BROWSER_EXECUTABLE` when its default Chrome path is unavailable, or install Playwright Chromium. See [operations](https://github.com/michael-borck/workready-deploy/blob/main/docs/operations.md#checks-before-publishing) for cross-repository checks.

## Publish and handle data

The deployed VPS uses the bundled image built by `workready-deploy`. Pushing this repository alone does not refresh that image. Follow the [image publication workflow](https://github.com/michael-borck/workready-deploy/blob/main/docs/operations.md#build-the-bundled-api-image-on-github).

Use synthetic profiles and resumes. Filtering is best-effort. Messages, task submissions and feedback persist for lecturer review, and selected cloud providers receive prompts. Retention cleanup is operator-triggered. See the [privacy guide](https://github.com/michael-borck/workready-deploy/blob/main/docs/privacy.md).

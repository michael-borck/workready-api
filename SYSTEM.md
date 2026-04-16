# WorkReady — System Map

> **Purpose of this document**: bring a new collaborator (human or LLM) up to
> speed on the entire WorkReady simulation in 5–10 minutes. Covers what every
> repo does, how they fit together, the non-obvious cross-cutting patterns,
> and where to look for common changes. Deeper API internals live in the
> Claude memory note `project_workready_api_architecture.md`.

## What WorkReady is

WorkReady is an AI-powered internship simulation built for Curtin
University's School of Marketing and Management. A student signs in with an
email, browses a Seek-style job board of fictional companies, applies with a
resume + cover letter, and is taken through the full arc of an internship —
resume review → hiring interview → work tasks with mentor feedback →
mid-placement coaching → informal team lunches → reflective exit interview —
entirely in a safe simulated environment. Six fictional companies hire from
the board; conversations are typed but properly back-and-forth via real LLM
calls. The whole thing is designed to fail safely so students can fumble an
interview, write a weak resume, freeze up in a group lunch, and learn from it
before the stakes are real.

## Repo inventory

All repos live as siblings under `loco-ensyo/`. The first six are the
runtime simulation; the next six are the company sites; the rest are
generators, deployment, and external tools.

| Path (relative to `loco-ensyo/`) | What it is | Tech |
|---|---|---|
| **Runtime simulation** |
| `workready-api/` | FastAPI backend — every state transition, every LLM call, every assessment, every notification dispatch. The keystone. | Python 3.13, FastAPI, SQLite |
| `workready-portal/` | Student portal — sign in, dashboard, inboxes, interview UI, lunchroom UI, exit interview UI, journey of every stage. | Vanilla JS + HTML + CSS, no build step |
| `workready-jobs/` | Seek-style job board ("seek.jobs") where students browse and apply. Has its own Quick Apply modal that POSTs to `workready-api`. | Vanilla JS, static build via `build.py` |
| `workready-primer/` | Ink-based interactive primer that introduces students to the simulation before they sign in. | Ink, TypeScript |
| `workready-deploy/` | Single-source-of-truth deployment. Installs and runs the entire simulation on one machine. See "Deployment" section below. | Bash, Docker, Caddy |
| **Company sites** (each is a hand-crafted site with its own design system, used as the "company intranet" once a student is hired) |
| `nexuspoint-systems/` | Cybersecurity / IT services. |
| `ironvale-resources/` | Mining and resources. |
| `meridian-advisory/` | Operations & strategy consulting. |
| `metro-council-wa/` | Local government. |
| `southern-cross-financial/` | Financial planning. |
| `horizon-foundation/` | Community / not-for-profit. |
| Each company repo: | Static site (`build.py` + Jinja2 templates), `brief.yaml` (authoritative content), `jobs.json` (ensayo export), `content/employees/{slug}-prompt.txt` (LLM personas). |
| **Generator + external tools** |
| `loco-ensayo/` | Ensayo — generates `jobs.json` from `brief.yaml` for each company. Also seeds `task_templates`, `employees`, `business_hours`. | Python |
| `talk-buddy/` | External Electron app for conversation rehearsal. WorkReady exports practice scenarios that students import here. Lives outside loco-ensyo at `~/Projects/talk-buddy`. | Electron, Next.js |
| `career-compass/` | External Electron app for resume-vs-job gap analysis. Lives outside loco-ensyo at `~/Projects/career-compass`. | Electron, Next.js |

## Where the data lives

- **Single SQLite DB** at `workready-api/workready.db` (path via `WORKREADY_DB`
  env). All state — students, applications, stage results, messages,
  interview sessions, tasks, lunchroom sessions/posts, calendar events. WAL
  mode, row factory enabled, foreign keys on.
- **No background workers.** Every "this happens later" feature uses lazy
  delivery: persist a row with a `deliver_at` ISO timestamp, filter
  `WHERE deliver_at <= now()` on read. See cross-cutting patterns below.
- **Personas + content** live in each company repo at
  `<company>/content/employees/<slug>-prompt.txt`. The API reads them at
  runtime via `SITES_DIR` env (defaults to `loco-ensyo/`).
- **Job listings** flow `<company>/brief.yaml` → ensayo → `<company>/jobs.json`
  → API loads via `load_jobs()` in `jobs.py`. Two locations exist in dev
  (`loco-ensyo/<slug>/jobs.json` and `workready-api/jobs/<slug>.json`) — keep
  both in sync when seeding manually.
- **No file uploads persisted long-term** for resumes — extracted text is
  stored on the application row, the PDF itself is processed in memory.
  (Mail attachments do persist via `mail.py`.)

## A student's full journey — what touches what

This is the canonical control flow. Numbers map to each touchpoint.

1. **Sign in** at `workready-portal` (any email, no password). Portal calls
   `GET /api/v1/student/{email}/state` → API creates the student row if new.

2. **Browse** `workready-jobs` (seek.jobs). Postings are seeded at API
   startup from each company's `jobs.json`. Job board calls
   `GET /api/v1/postings` and renders cards.

3. **Apply** via the Quick Apply modal in `workready-jobs`. POSTs PDF +
   form fields to `POST /api/v1/resume`. API extracts PDF text, runs the
   resume assessor (LLM or stub), creates an `application` row + a
   `stage_results` row, dispatches a confirmation message via `notify()`,
   and either advances stage to `interview` or sets status to `rejected`
   with feedback.

4. **Resume outcome** lands in the student's **personal** inbox in the
   portal (Inbox tab). The portal polls `GET /api/v1/inbox/{email}` and
   renders messages whose `deliver_at` has passed.

5. **Interview** (Stage 3) — student opens the Interview view in the
   portal, which calls `POST /api/v1/interview/start`. API resolves the
   manager character from the job's `manager_persona` field, builds a system
   prompt via `interview.build_interview_system_prompt()`, creates an
   `interview_sessions` row with `kind='hiring'`, fires the opening turn.
   Each subsequent turn: portal `POST /api/v1/interview/message` → API
   appends + re-builds prompt + `chat_completion()` → reply. End:
   portal `POST /api/v1/interview/{id}/end` → API runs `assess_interview()`,
   records a `stage_result`, advances or rejects, drops feedback in the
   personal inbox.

6. **Hired**. API calls `placement.activate_work_placement(application_id)`
   which creates 3 gated `tasks` rows (only task 1 visible) and drops a
   welcome email + first task brief in the **work** inbox.

7. **Tasks** (Stage 4) — student opens the Tasks view, submits each via
   `POST /api/v1/tasks/{id}/submit` (PDF or text body). API runs
   `task_reviewer.review_task_submission()`, persists with a
   `review_deliver_at`, reveals the next task with a smaller delay so the
   next brief lands before the prior feedback. Task feedback emails come
   from the mentor character (`reports_to` from the job listing).

8. **Mid-placement coaching** (between task 2 and task 3) — fires
   automatically on task 2 submission. Drops a "quick check-in" message in
   the work inbox. Student opens the "Mid-placement check-in" view in the
   portal, has a 5-turn coaching conversation with their mentor via
   `POST /api/v1/perf-review/*` routes. Output is `coaching_notes` +
   `key_focus`, both feed into Stage 6 later.

9. **Lunchroom** (Stage 5) — also fires on each task review (Stage 5a). API
   calls `lunchroom.create_invitation()` which drops a casual invitation in
   the work inbox with 3 proposed lunchtime slots. Student opens the
   Lunchroom view, picks a slot or declines. When entry window opens, the
   student clicks "Enter the lunchroom" → `POST /api/v1/lunchroom/session/
   {id}/activate` → API plans an interleaved beat arc across participants
   and persists each as `lunchroom_posts` rows (Stage 5b). Portal polls
   `GET /chat` every 3s; each poll runs `deliver_due()` which renders any
   due beats via `chat_completion()` using persona files. Student types
   replies via `/post` — `@mentions` reschedule the addressed character's
   next beat forward. On hard cap or no pending beats, `_maybe_complete()`
   runs `_run_review()` which produces `participation_notes` + a warm
   `system_feedback` message that lands in the work inbox (Stage 5c).

10. **All tasks done** — `submit_task` detects `is_final_task`, advances
    stage to `exit`, drops a "wrapup conversation ready" message
    in the work inbox.

11. **Exit interview** (Stage 6) — student opens the Exit Interview view.
    `POST /api/v1/exit/start` builds a journey context via
    `exit_interview.build_journey_context()` which gathers resume + interview
    + tasks + lunchroom participation_notes + perf review coaching_notes.
    System prompt sets up Sam Reilly (HR) — deliberately a different
    character than the hiring manager. 8-turn reflective conversation. End
    runs `assess_exit_interview()` (scores **self-awareness**, not
    performance), flips application status to `completed`, drops a warm
    summary in the personal inbox.

12. **Lecturer journey report** — admin loads the admin page, finds the
    student, clicks "Journey report" on an application. API endpoint at
    `GET /api/v1/admin/applications/{id}/journey-report` returns a
    structured report (resume → interview → tasks → perf review →
    lunchroom → exit interview + chronological timeline). Portal renders it
    as a printable view with `@media print` styles.

## Cross-cutting patterns (read these once)

These show up everywhere. Don't reinvent them.

- **Lazy delivery.** Anything that "happens later" is a row with a
  `deliver_at` ISO timestamp, filtered `WHERE deliver_at <= now()` on read.
  Used for: message inboxes (`messages.deliver_at`), task review feedback
  (`task_submissions.review_deliver_at`), task reveal (`tasks.visible_at`),
  lunchroom beats (`lunchroom_posts.deliver_at`). **Never reach for celery
  or apscheduler** — there is no background worker, that's deliberate.
- **LLM stub mode.** `LLM_PROVIDER=stub` runs the entire simulation with no
  API keys via deterministic stubs. Each conversation surface that needs
  reflective/coaching/casual tone has its own stub bypass because the
  shared `interview._stub_reply` is hardcoded for hiring interviews. See
  `lunchroom_chat._render_beat`, `exit_interview.chat_completion_for_exit`,
  `performance_review.chat_completion_for_review`. Don't remove the bypass.
- **`notify()` adapter** in `notifications.py` is the single dispatch point
  for all student communications. Defaults to in-app inbox; future channels
  (real email, Telegram, Teams) plug in as new handlers without touching
  call sites. Lunchroom messages bypass this because they live in the
  **work** inbox not the personal one.
- **The `kind` column.** `interview_sessions.kind` distinguishes
  `'hiring'` (Stage 3), `'exit'` (Stage 6), and `'performance_review'`
  (mid-placement coaching). One table, three conversation kinds, parallel
  routes per kind.
- **Blocking model** in `blocking.py` decides which companies/roles are
  "off the board" for a student. Blocks on: rejected applications (per
  `BLOCK_ON_*` env vars — role or company level), resigned applications
  (always company), completed applications (always company — you don't redo
  a placement). Used by seek.jobs to grey out cards.
- **`_effective_task_status`** in `app.py` is the lazy-gate driver for
  task feedback — until `review_deliver_at` passes, the student sees
  `under_review` even though the LLM-generated review is already on disk.

## The four conversation surfaces

| Surface | Stage | Module | Persona | Turns | Assessor focus |
|---|---|---|---|---|---|
| Hiring desk chatbot | 1 (browse) | AnythingLLM workspace (separate, not in this codebase) | Generic company "receptionist" with RAG over career page | open-ended | n/a — purely informational |
| Hiring interview | 3 | `interview.py` | Hiring manager (`reports_to` from job listing, `manager_persona` field) | ~10 | Job fit |
| Mid-placement coaching | 4 (between task 2 & 3) | `performance_review.py` | Mentor (same `reports_to` character — they reviewed your tasks) | ~5 | Coaching responsiveness, surfaces a `key_focus` for task 3 |
| Exit interview | 6 | `exit_interview.py` | Sam Reilly, Head of People (deliberately different from hiring manager so student feels safe being honest) | ~8 | Self-awareness / reflection |

## External tools (sidebar links + practice exports)

- **Talk Buddy** (`~/Projects/talk-buddy`) — local Electron app. WorkReady
  exports practice scenarios from `talk_buddy_export.py` for the hiring
  interview and lunchroom (one scenario per AI participant). Routes:
  `GET /api/v1/practice/interview/{application_id}/talk-buddy.json` and
  `GET /api/v1/practice/lunchroom/{session_id}/talk-buddy.json`. Portal
  surfaces these as "Practice in Talk Buddy" buttons on the interview
  pre-screen and lunchroom invitation cards.
- **Career Compass** (`~/Projects/career-compass`) — local Electron app for
  resume gap analysis. No deep-link (no protocol handler), so the workflow
  is copy-paste. Discoverability surfaces: a tip block above the resume file
  picker on the seek.jobs apply modal, AND a mention in the resume **failure**
  feedback footer (passing students see Talk Buddy instead).
- **AnythingLLM** — separate install. `setup-chatbots.py` script in
  `workready-api` creates one "hiring desk" workspace per company.

## Where to look for X

- **Add a new company** → create a `<slug>/` repo under `loco-ensyo/` with
  `brief.yaml` and a Jinja2 site, run `ensayo export-jobs` to produce
  `jobs.json`, add the slug to `SITE_SLUGS` in `workready-api/app.py`, add
  the URL to `COMPANY_URLS` in `workready-portal/config.js`, add to
  `SECTOR_MAP` in `workready-jobs/src/config.js`.
- **Add a new role within a company** → edit that company's `brief.yaml`,
  re-run `ensayo export-jobs`. The API picks it up at next startup.
- **Tune lunchroom timing** → env vars in `scheduling.py`:
  `LUNCHROOM_BEAT_INTERVAL_SECONDS`, `LUNCHROOM_OPENING_DELAY_SECONDS`,
  `LUNCHROOM_HARD_CAP`, `LUNCHROOM_INVITES`, etc.
- **Tune task pacing** → `TASK_FEEDBACK_DELAY_MINUTES`,
  `TASK_NEXT_TASK_DELAY_MINUTES` (+ jitter pairs).
- **Add a new conversation surface** → mirror `exit_interview.py` and
  `performance_review.py`: new module + new routes in `app.py` + new
  `kind` value in `interview_sessions` + portal view (mirror the existing
  Stage 3 DOM and reuse the `interview-msg` CSS classes).
- **Add a new notification channel** → write a handler function and call
  `register_channel('email', handler)` in `notifications.py`. Update
  `_EVENT_ROUTES` if specific events should also fire on the new channel.
- **Inspect a student's state** → admin page at
  `workready-portal/admin.html`, gated by `WORKREADY_ADMIN_TOKEN`. Force
  state, flush pending messages, force-pass/fail stages, journey report.
- **Block / unblock a company for a student** → it's derived state, not
  stored — driven by their application history. To "unblock" a student,
  use the admin reset endpoint or delete the offending application row.
- **Switch LLM providers** → `LLM_PROVIDER=anthropic|openrouter|ollama|stub`
  + provider-specific keys (`ANTHROPIC_API_KEY`, etc.). Same env affects
  every conversation surface.

## Local dev quickstart

```bash
# 1. API
cd workready-api
WORKREADY_DB=/tmp/wr.db LLM_PROVIDER=stub uv run uvicorn workready_api.app:app --port 8000

# 2. Portal (any static server works)
cd workready-portal
python3 -m http.server 8001
# Then open http://localhost:8001 — sign in with any email

# 3. seek.jobs (only needed if testing the apply flow end to end)
cd workready-jobs/dist
python3 -m http.server 8002

# 4. Quick smoke test of the full pipeline (no LLM needed)
WORKREADY_DB=/tmp/wr.db LLM_PROVIDER=stub uv run python -c "
from workready_api.db import init_db, get_or_create_student
init_db()
s = get_or_create_student('test@example.com', 'Test Student')
print('student id:', s['id'])
"
```

`LLM_PROVIDER=stub` is critical for dev — every conversation surface has a
deterministic stub so you can run the full simulation with no API keys.

## Deployment (`workready-deploy/`)

Single-machine deployment. Caddy reverse-proxies 9 virtual hosts in front of
one FastAPI container plus static-file serving for the portal, seek.jobs, and
all six company sites. Three tiers, each adding capability without breaking
the previous:

| Tier | What it adds | Requires | Audience |
|---|---|---|---|
| **1 — Demo (zero config)** | Everything runs on `:80` with path-based routing, no DNS, builtin keyword chatbot, stub LLM. `docker compose up -d` and you're done. Pre-built image ships on GHCR. | Docker | Lecturer evaluation, tutorials, demos |
| **2 — Real LLM + DNS** | Per-host domains via `domains.env`, real LLM provider (Anthropic / OpenRouter / Ollama), TLS via Caddy auto-cert. | Docker, DNS, LLM keys | Pilot cohort |
| **3 — AnythingLLM + RAG hiring desks** | Adds AnythingLLM for the per-company hiring desk chatbots with RAG over each company's career page. `setup-chatbots.py` provisions one workspace per company. | Tier 2 + AnythingLLM container | Production teaching |

The full tier breakdown lives in `workready-deploy/DEPLOY-TIERS.md`.

**Key files in `workready-deploy/`:**

- `install.sh` — single source of truth. Works identically when piped from
  curl on a bare-metal VPS or `RUN`-ed inside `Dockerfile` during build.
  Clones the 9 simulation repos (6 company sites + workready-api +
  workready-portal + workready-jobs), builds the static sites, sets up the
  Python venv, runs the API. Env vars: `WORKREADY_DIR` (install root,
  default `/opt/workready`), `GITHUB_ORG`, `SKIP_DEPS`, `SKIP_CLONE`.
- `Dockerfile` — wraps `install.sh` in a Debian base image. Built and
  pushed to GHCR via GitHub Actions.
- `docker-compose.yml` — single `workready` service that runs everything
  (Caddy + API + static sites) inside one container. Tier 1 just does
  `docker compose up`.
- `Caddyfile` — 9 virtual hosts (6 companies + portal + seek + api), each
  with `{$DOMAIN_X:fallback.localhost}` env substitution so the same file
  works in tier 1 (localhost fallbacks) and tiers 2/3 (real domains via
  `domains.env`). Static sites are file-served from
  `/opt/workready/<company>/dist`; the API host reverse-proxies to the
  uvicorn process inside the container.
- `domains.env.example` — template showing every env var Caddy expects.
  Copy to `domains.env` and fill in real domains for tier 2+.
- `setup-chatbots.py` — provisions one AnythingLLM workspace per company
  and uploads career-page content for RAG. Tier 3 only.
- `start.sh` — process supervisor inside the container. Boots Caddy and
  uvicorn together.
- `README.md` + `DEPLOY-TIERS.md` — operator-facing docs.

**State and persistence in production:**

- The SQLite DB lives at `/opt/workready/workready-api/workready.db` inside
  the container. Mount this as a volume in `docker-compose.yml` if you want
  it to survive container rebuilds (it does in the shipped compose file).
- Mail attachments live in the same install dir under per-student
  subdirectories. Same volume mount covers them.
- Static company-site builds live in `/opt/workready/<company>/dist` and
  are rebuilt on container start by `install.sh`.

**Production env vars worth knowing about** (all set via
`docker-compose.yml` env or `.env` file):

- `LLM_PROVIDER`, `LLM_MODEL`, `ANTHROPIC_API_KEY` etc. — without these,
  every conversation surface degrades to its stub.
- `WORKREADY_ADMIN_TOKEN` — required for the admin page to do anything.
  If unset, every `/admin/*` endpoint returns 503.
- `DOMAIN_*` — the 9 virtual host domains. See `domains.env.example`.
- `INTERVIEW_BOOKING_ENABLED` — turn on the booking flow for cohorts that
  need scheduled interviews. Off by default.
- `LUNCHROOM_OCCASIONS` — comma-separated allow-list to pin a demo vibe
  (e.g. `routine_lunch,task_celebration` to skip cultural events).

**Updating production:**

- Pull the new GHCR image and `docker compose up -d` — the install script
  re-runs on container start so any new content from the company-site
  repos is picked up. The DB volume survives.
- For schema changes, the migration runner in `db.py:_migrate()` is
  idempotent — it adds columns to existing DBs on init and is safe to run
  on container start. New migrations should follow the existing
  add-column-with-IF-NOT-EXISTS pattern.

## Configuration knobs

All env-driven, all defined in `workready_api/scheduling.py`. Headline groups:

- **Interview booking**: `INTERVIEW_BOOKING_ENABLED`, `BUSINESS_HOURS_*`,
  `BUSINESS_DAYS`, `TIMEZONE`, `SLOT_DURATION_MINUTES`, `SLOTS_OFFERED`,
  `LATE_GRACE_MINUTES`, `MAX_MISSED_INTERVIEWS`, `MAX_RESCHEDULES`.
- **Delays**: `RESUME_FEEDBACK_DELAY_MINUTES`, `INTERVIEW_FEEDBACK_DELAY_*`,
  `TASK_FEEDBACK_DELAY_*`, `TASK_NEXT_TASK_DELAY_*` (each with a JITTER pair).
- **Tasks**: `TASKS_PER_STUDENT` (always 3 currently), `TASK_DEADLINE_DAYS`.
- **Lunchroom**: `LUNCHROOM_INVITES`, `LUNCHROOM_DECLINE_LIMIT`,
  `LUNCHROOM_TRIGGER`, `LUNCHROOM_INCLUDE_MENTOR`,
  `LUNCHROOM_PARTICIPANT_COUNT`, `LUNCHROOM_INVITE_LEAD_HOURS`,
  `LUNCHROOM_INVITE_HORIZON_DAYS`, `LUNCHROOM_TIME_OF_DAY_*`,
  `LUNCHROOM_SLOTS_OFFERED`, `LUNCHROOM_EARLY_ENTRY_MINUTES`,
  `LUNCHROOM_LATE_ENTRY_HOURS`, `LUNCHROOM_SOFT_CAP`, `LUNCHROOM_HARD_CAP`,
  `LUNCHROOM_BEAT_INTERVAL_SECONDS`, `LUNCHROOM_BEAT_JITTER_SECONDS`,
  `LUNCHROOM_OPENING_DELAY_SECONDS`, `LUNCHROOM_MENTION_RESCHEDULE_SECONDS`,
  `LUNCHROOM_BEATS_PER_CHAR_*`, `LUNCHROOM_OCCASIONS`.
- **Lifecycle**: `MAX_CYCLES` (default 3) — re-application cap.
- **Blocking**: `BLOCK_ON_RESUME_FAILURE`, `BLOCK_ON_INTERVIEW_FAILURE`,
  `BLOCK_ON_TASK_FAILURE` — each `none|role|company`.
- **LLM**: `LLM_PROVIDER`, `LLM_MODEL`, provider-specific keys.
- **Admin**: `WORKREADY_ADMIN_TOKEN` — required for any `/admin/*` endpoint.
- **Storage**: `WORKREADY_DB`, `SITES_DIR`.

## Things that look like they exist but don't

- No background workers / cron / queue. Every "later" feature is lazy-gated.
- No session-based auth — every endpoint takes the student email as a
  param. There's no password.
- No multi-student team tasks. Each student goes through alone.
- No video/voice — every conversation is typed.
- No aggregate grade in the journey report — by design. Lecturers grade.
- No PR review / cohort dashboard yet — the admin page is the current
  surface.
- `INTERVIEW_INVITATION_DELAY_MINUTES` is defined in scheduling.py but
  never read anywhere. Don't confuse with `INTERVIEW_FEEDBACK_DELAY_MINUTES`.

## Phased roadmap (current state and beyond)

- **Phase 1 (current)** — prove the educational pipeline. Six stages,
  lifecycle, journey report, practice tools. Feature-complete as of
  April 2026.
- **Phase 2** — productionise: real email delivery, passwordless magic-code
  auth, rate limiting, admin bulk cohort management.
- **Phase 3** — scale: inbound email, Telegram channel, per-student
  notification preferences, per-cohort config, light LMS integration.
- **Phase 4** — institutional: MS Teams via Graph API, Curtin SSO, full
  gradebook pass-back, proper lecturer dashboard.

24-month runway across phases 2–4. The educational pipeline is sequenced
first so production polish and institutional integration don't block teaching
improvements.

## Where to go for deeper detail

- **`STAGES-4-5-6.md`** in this repo — the original design doc for stages 4
  through 6. Some details have shifted but the spirit is intact.
- **Claude memory note `project_workready_api_architecture.md`** — deeper
  internals of the API itself: lazy delivery patterns by name, LLM call
  pattern, FastAPI app structure, individual stage internals, the four
  conversation surfaces in code-level detail.
- **Claude memory note `project_workready.md`** — high-level project
  overview, current state, key decisions, future phases.
- **`workready-deploy/install.sh`** — single source of truth for production
  deployment.

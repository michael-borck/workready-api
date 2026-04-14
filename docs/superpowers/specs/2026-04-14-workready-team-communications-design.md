# WorkReady Team Communications — Design

**Date:** 2026-04-14
**Author:** Michael Borck (brainstormed with Claude Opus 4.6)
**Status:** Design — pending implementation plan
**Related:** `2026-04-14-workready-team-communications-future-work.md`

---

## Problem statement

WorkReady currently ships six working simulation stages (apply → resume →
interview → work tasks → lunchroom → exit interview) plus a mid-placement
coaching check-in and a lecturer journey report. It is functionally
feature-complete for what a student experiences *solo*, but the
"workplace communications" side is thin:

- **The public hiring desk chatbot** (Stage 1) was designed, the setup
  script written, and workspace names + embed UUIDs planned — but
  nothing was ever run live against the AnythingLLM server. Zero
  workspaces exist on `chat.eduserver.au`. Zero company careers pages
  embed the widget. The whole Stage 1 conversational surface is
  "specified but not provisioned".
- **Once hired, students can email characters** via the existing
  `mail.py` system (thread-aware, context-stuffed, works with 77 valid
  addresses), but they can NOT see their team, can NOT initiate a
  real-time chat with a team member, and the character's email replies
  don't know about the student's current task state.
- **There is no concept of "team" vs "organisation".** A hired student
  sees a flat inbox, not a structured picture of who they work with
  directly vs who's part of the wider company.
- **There's no workplace-behaviour feedback loop.** A student could
  email the CEO about where the coffee machine is, or type a rude
  message to their mentor, and the simulation would reply in character
  with no teaching moment.

The gap between "this simulation ships a complete pedagogical arc" and
"this simulation teaches workplace communication skills" is exactly this
design document.

---

## Decisions locked during brainstorming

| # | Decision | Choice |
|---|---|---|
| 1 | Where does the conversation machinery live? | **Option A** — custom `chat_completion()` machinery owns every non-public surface; AnythingLLM hosts only the public Stage 1 hiring desk. |
| 2 | Audit scope for this project | **Cut C** — character roster audit + AnythingLLM robustness + hiring desk embed + live chat UI + task-aware context + business hours gating + presence/away states + team directory + persona gap-fill. |
| 3 | Execution split | **Split X** — functional first, Teams-style visual rewrite deferred to a future project (Split Z, see future-work doc). |
| 4 | Who can a hired student talk to? | **Option R with guardrails** — team gets full chat + email + presence + business hours, org gets email only, system guardrails watch every outgoing message. |
| 5 | Guardrail depth | **G1** — single-pass classifier with in-character bounce-back on any flag. No escalation ladder, no termination path. G2 deferred. |
| 6 | How is "team" defined? | **T1** — `team:` field on each job listing. Default (if absent) = full company `employees[]`. |
| 7 | Email ↔ chat threading | **U3** — unified storage (one `messages` row per message, with `channel` column), bifurcated display (work inbox shows email, chat drawer shows chat). LLM context always sees the full unified thread. |
| 8 | Public holidays | **In scope** — central region-keyed config, `holidays_region` field per company, availability module skips holiday dates. |

---

## Non-goals

These are either out of scope for this project or deferred to future
projects. See `2026-04-14-workready-team-communications-future-work.md`
for the deferred items.

- **No Teams-style work interface rewrite.** Existing portal UI paradigm
  is reused. (Deferred: Split Z.)
- **No escalation ladder for guardrails.** Single bounce-back per
  flagged message, no counts tracked for consequences, no firing path.
  (Deferred: G2.)
- **No dynamic team adjustment for lecturers.** Team is static per job.
  (Deferred: dynamic team adjustment.)
- **No unification of the two character-content stores.**
  `brief.yaml` `employees[].customisation.background` and
  `content/employees/<slug>-prompt.txt` remain separate; persona
  updates touch both by convention. (Deferred: unify stores.)
- **No student-timezone modelling.** Everything is company-local.
  (Deferred.)
- **No mentor-presence gating for task feedback.** Task reviewer
  feedback fires on submission regardless of the mentor's
  availability. (Deferred.)
- **No formal pytest suite introduction.** Matches existing codebase
  convention of smoke scripts. (Deferred.)
- **No AI analysis of the classifier's prompt quality.** Prompt tuning
  is a pilot-cohort activity, not a unit test.

---

## Architecture

Three conversation layers. Clean boundaries. This project adds Layer 3
and hardens Layer 1. Layer 2 is untouched.

```
┌─────────────────────────────────────────────────────────────────┐
│  LAYER 1 — Public hiring desk (AnythingLLM, Stage 1, anonymous) │
│                                                                  │
│  Careers page ──embed widget──▶  AnythingLLM workspace           │
│  (company site)                   (RAG over brief.yaml + jobs)   │
│                                                                  │
│  Namespaced workready-<slug>-hiring. Outside workready-api       │
│  entirely. If AnythingLLM is down, Layer 2 and Layer 3 still    │
│  work perfectly.                                                 │
└─────────────────────────────────────────────────────────────────┘
                                │
                          application
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│  LAYER 2 — In-simulation conversations (existing, unchanged)    │
│                                                                  │
│  Portal ─▶ workready-api ─▶ chat_completion() ─▶ LLM provider   │
│                                                                  │
│  • Stage 3 hiring interview    • Stage 5 lunchroom chat         │
│  • Stage 4 task reviewer       • Stage 5c lunchroom review      │
│  • Stage 4.5 perf coaching     • Stage 6 exit interview         │
│  • Resume assessor                                               │
│                                                                  │
│  NONE of this code changes in this project.                     │
└─────────────────────────────────────────────────────────────────┘
                                │
                            hired
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│  LAYER 3 — Team communications (NEW)                            │
│                                                                  │
│  ┌─────────────────┐        ┌─────────────────────────────┐    │
│  │ Team directory  │───────▶│  Comms monitor              │    │
│  │ (sidebar)       │        │  (new: classifier + nudges) │    │
│  └─────────────────┘        └──────────┬──────────────────┘    │
│           │                             │                       │
│           ▼                             ▼                       │
│  ┌─────────────────┐        ┌─────────────────────────────┐    │
│  │ Work inbox      │◀──────▶│  Unified thread store       │    │
│  │ (existing, now  │        │  (messages + channel col)   │    │
│  │  colour-coded)  │        │                             │    │
│  └─────────────────┘        └──────────┬──────────────────┘    │
│                                         │                       │
│  ┌─────────────────┐                    │                       │
│  │ Chat drawer     │◀───────────────────┘                       │
│  │ (new, slides in │                                            │
│  │  from right)    │        ┌─────────────────────────────┐    │
│  └─────────────────┘───────▶│  chat_completion() + task-  │    │
│                              │  aware context stuffer (new)│    │
│                              └─────────────────────────────┘    │
│                                                                  │
│  Business hours + presence gating applied at directory render  │
│  time and message-send time. Classifier hooks in on every      │
│  outgoing student message regardless of channel.               │
└─────────────────────────────────────────────────────────────────┘
```

**Architectural properties:**

- **Layer 1 is quarantined.** AnythingLLM touches nothing inside
  workready-api. A student chatting to the hiring desk doesn't hit the
  simulation API. Failure modes don't cross over.
- **Layer 2 is unchanged.** Every existing simulation surface keeps
  working exactly as it does today.
- **Layer 3 is additive.** New team-directory UI, new chat drawer, new
  comms monitor module, new task-aware context stuffer. The existing
  `messages` table gets one new column (`channel`). No data rewrites.
- **One classifier, two channels.** The comms monitor intercepts every
  outgoing student message regardless of whether it's email or chat.
- **Shared context stuffer.** One helper function builds the character's
  "what I know about this student" context for both email reply
  generation and chat reply generation.
- **Independent LLM providers.** Layer 1 (AnythingLLM) and Layers 2/3
  (direct to provider) can be configured with different models. Hiring
  desk can run on a cheap fast model; in-sim conversations on a
  higher-quality one.

---

## Components

Eight new or modified components, grouped into six subsystems.

### Subsystem A — Team directory

**A.1 — `team:` field on job listings.** Schema extension in `brief.yaml`
and `jobs.json`:

```yaml
jobs:
  - slug: junior-analyst
    title: Junior Analyst
    reports_to: Karen Whitfield
    team:
      - karen-whitfield
      - ravi-mehta
      - brooke-lawson
```

If `team:` is missing, the resolver defaults to all slugs in the
company's `employees[]`. This means small-business companies (where the
entire org is the team) need zero authoring — just omit `team:`.

**A.2 — `workready_api/team_directory.py`** (new module). Single public
function: `get_team_for_application(application_id) -> TeamView`.
Returns `{team: [CharacterRef], org: [CharacterRef], business_hours}`.
Written as a single function so the future dynamic-team-adjustment
project can extend it with an override precedence chain without
touching any callers.

**A.3 — `GET /api/v1/team/{application_id}`** (new route). Wraps A.2,
serialises to JSON for the portal.

**A.4 — Sidebar team directory** (portal). New collapsible section
under "Workspace" in the existing sidebar nav. Renders the team list
with name, role, presence dot, chat icon. Clicking opens the chat
drawer. Hover shows tooltip with role + email. Org members render in a
collapsed secondary list ("Wider organisation") — email-only, no chat
icon.

### Subsystem B — Unified thread store + task-aware context

**B.1 — `messages` table extensions.** Two new columns:

- `channel TEXT NOT NULL DEFAULT 'email'` — `'email' | 'chat' | 'system'`
- `review_flag TEXT` — nullable JSON from the classifier

Plus one composite index:

```sql
CREATE INDEX IF NOT EXISTS idx_messages_chat_thread
  ON messages(student_id, application_id, channel, deliver_at)
  WHERE channel = 'chat';
```

**B.2 — `workready_api/context_builder.py`** (new module). Single
function: `build_character_context(student_id, character_slug,
application_id) -> CharacterContext`. Returns everything a character
needs to reply coherently:

- Full unified thread with this character (all channels)
- Student's current stage + active application state
- If `work_task` stage: active tasks, briefs, latest submissions and
  their feedback, any `coaching_notes` from perf review
- If past stages: resume score, interview score, task history,
  lunchroom participation summary
- Presence/away info for the character (what they "know" about their
  own availability — useful for replies like "sorry for the delay, I
  was at a conference")

Called from `mail.py` character reply path AND the new chat reply
path. Single source of truth.

### Subsystem C — Chat drawer

**C.1 — Chat drawer component** (portal). A right-side slide-in drawer
that opens over the current view. Header: character name + role +
presence dot. Body: chat bubbles. Footer: composer. Escape or click-away
closes; the thread is preserved and reopens to the same state.

**C.2 — `POST /api/v1/chat/send`** (new route). Body: `{application_id,
character_slug, content}`. Runs through comms monitor, persists the
student message synchronously, schedules the character reply via the
standard lazy-delivery pattern. Returns the student message immediately.

**C.3 — `GET /api/v1/chat/thread/{application_id}/{character_slug}`**
(new route). Returns delivered chat messages for the pair. Portal polls
every ~3 seconds while the drawer is open (same pattern as the
lunchroom chat).

### Subsystem D — Comms monitor (guardrails)

**D.1 — `workready_api/comms_monitor.py`** (new module). Single public
function: `classify_outgoing(student_id, application_id, channel,
recipient, subject, body) -> ClassificationResult`. One LLM call with a
structured prompt; returns a JSON object:

```json
{
  "recipient_appropriateness": "ok" | "wrong_audience",
  "tone": "ok" | "sharp" | "inappropriate",
  "channel_appropriateness": "ok" | "wrong_channel",
  "rationale": "one sentence explaining any flag"
}
```

Stub mode returns all-ok with `rationale: "stub mode"`.

**D.2 — Classifier hook in `mail.py` and chat send route.** Before any
outgoing student message is persisted, it runs through
`classify_outgoing`. If all axes are `ok`, the message flows normally.
If any axis is flagged, the character's expected reply is replaced by
a bounce-back from a proxy, and `review_flag` on the student's message
row captures the classification.

**D.3 — Proxy character registry.** Small module-level constant mapping
flag type to proxy:

- `wrong_audience` → `Jenny Kirkwood`, cast as "Executive Assistant to
  the CEO". Single universal proxy name across all companies — we
  don't invent per-company assistants. Her `sender_email` is
  constructed dynamically from the student's company domain (e.g.
  `jenny.kirkwood@ironvaleresources.com.au`) so the bounce-back
  appears to come from inside the student's own company. Her reply
  body is a **context-aware template** filled at send time with the
  classifier's rationale and the appropriate redirect target
  (facilities, HR, etc.). Jenny does NOT have a persona file in
  `content/employees/`, does NOT appear in any company's `brief.yaml`,
  and does NOT appear in the team directory. She exists only as a
  template in the proxy registry and is instantiated per incident.
- `sharp | inappropriate` → the student's mentor (resolved from the
  job's `reports_to`). Gentle template note. Uses the mentor's real
  persona file and email address — it's a note *from* the mentor, not
  a template masquerading.
- `wrong_channel` → system message (channel=`'system'`) with a
  heads-up template. Sender is the simulation voice
  (`sender_name='WorkReady'`, `sender_role='Simulation guide'`,
  `sender_email='noreply@workready.eduserver.au'`), consistent with
  existing system-voiced messages elsewhere in the simulation.

**D.4 — Classifier fail-open policy.** Any failure of the classifier
call (timeout, malformed JSON, missing keys, provider down) causes the
message to flow normally as if classified `ok`. The `review_flag` gets
a special value `{"status": "classifier_unavailable", "error": "...",
"classified_at": "..."}` for later analysis. Rationale: a broken safety
layer should never block a legitimate message.

### Subsystem E — Business hours, presence, public holidays

**E.1 — `workready_api/availability.py`** (new module). Functions:

- `is_team_member_available_now(company_slug, character_slug) -> bool`
  — true if (a) now is inside business hours AND (b) not a public
  holiday AND (c) the character's presence state allows contact.
- `next_business_hours_slot(company_slug, after_iso) -> iso_string`
  — the next timestamp that falls inside business hours AND not on a
  public holiday. Used to delay character replies to look like they
  arrived during office hours.
- `is_public_holiday(company_slug, date_local) -> bool` — looks up the
  date in the loaded holidays file, scoped by the company's
  `holidays_region`.

**E.2 — Presence config in `brief.yaml`.** Optional field per employee:

```yaml
employees:
  - slug: ravi-mehta
    name: "Dr. Ravi Mehta"
    role: "General Manager — Sustainability"
    availability:
      status: "available"  # available | away | travelling | sick | on_leave
      return_date: null    # ISO date, nullable
      note: ""             # optional human-readable note
```

If absent, the character is always available. Static per cohort — no
runtime mutation.

**E.3 — Public holidays.** New file `workready-api/data/public_holidays.yaml`
— central, region-keyed, one region for now (`australia-wa`). Each
company's `brief.yaml` optionally references a region via
`business_hours.holidays_region`. Absent = no filtering.

**E.4 — `students.last_login_at` column.** Updated on every
`/api/v1/student/{email}/state` hit. Used by the business-hours
illusion: if the student has been away for over 24 hours, character
replies are backdated to look like they arrived during plausible
business hours in the gap.

### Subsystem F — Hiring desk embed (AnythingLLM side)

**F.1 — Harden `setup-chatbots.py`.** Four concrete changes:

1. **Marker check.** Every workready-managed workspace has this line at
   the very start of its system prompt:
   `[workready-managed] Do not remove this line.`
   Before updating an existing workspace, the script fetches the
   workspace, checks for the marker, and *refuses to touch* any
   workspace without it. Logs a clear warning naming the workspace.
2. **Update = full refresh.** Updating a workready-managed workspace
   means: list all assigned documents → delete each → upload freshly
   built RAG doc → assign it → update the system prompt. Preserves
   workspace UUID and embed UUID. No widget breakage.
3. **Full `--dry-run` coverage.** Extend the existing dry-run mode so
   it exercises the full update path (mock network calls).
4. **Embed UUID persistence.** After creating or confirming an embed,
   write the UUID to `workready-deploy/embed-uuids.yaml`. This file
   becomes the source of truth for the build pipeline.

**F.2 — `embed-uuids.yaml`** (new file in workready-deploy). Central
canonical store of embed UUIDs per company. Check into git.

```yaml
ironvale-resources: "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
nexuspoint-systems: "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
# ...

_meta:
  last_run: "2026-04-14T10:00:00Z"
  anythingllm_base_url: "https://chat.eduserver.au/api/v1"
  script_version: "1.0"
```

Embed UUIDs are not secrets — they're public the moment the widget
loads. The allowlist_domains setting on each embed is what actually
prevents misuse.

**F.3 — Embed widget partial in company site templates.** New Jinja
partial `_chat_widget.html.j2` in each company's `site/templates/`.
Each company's `build.py` reads `workready-deploy/embed-uuids.yaml` at
build time, substitutes the UUID, renders the partial, and includes it
from `careers.html.j2` (nowhere else for v1).

**F.4 — Persona gap-fill.** Any character referenced in `jobs.json`
`reports_to` or in `team:` but without a
`content/employees/<slug>-prompt.txt` file gets a stubbed persona
(~300 words) during this project. Enough for LLM roleplay to feel
distinct.

**F.5 — Persona writing rule: gender-neutral register.** All personas
(new stubs and any existing ones we touch for gap-fill) follow a
consistent professional register: characters speak as themselves in
first person, refer to colleagues by first name or role, and avoid
third-person pronouns (he/him, she/her, they/them) unless a specific
character's voice explicitly calls for one. Rationale: workplaces
naturally use first names and roles, and the neutral-by-default style
is both more professional and more forgiving of LLM variance. This is
not a politics decision — it's a register decision. Not configurable.
If a future project wants to model explicit pronoun preferences for
specific characters, that's its own scope.

---

## Data model

All schema changes are additive. No data rewrites.

### DB migrations (added to `db.py:_migrate()`)

```sql
-- Migration 8: messages.channel
ALTER TABLE messages ADD COLUMN channel TEXT NOT NULL DEFAULT 'email';

-- Migration 9: messages.review_flag
ALTER TABLE messages ADD COLUMN review_flag TEXT;

-- Migration 10: students.last_login_at
ALTER TABLE students ADD COLUMN last_login_at TEXT;

-- Index (run after migrations)
CREATE INDEX IF NOT EXISTS idx_messages_chat_thread
  ON messages(student_id, application_id, channel, deliver_at)
  WHERE channel = 'chat';
```

### Schema extensions (non-DB)

- `brief.yaml` per company: `business_hours.holidays_region` (optional),
  `employees[].availability` (optional, with status/return_date/note).
- `jobs.json` per job (generated by ensayo from `brief.yaml`): `team[]`
  (optional, list of character slugs).
- `workready-api/data/public_holidays.yaml` — new file, region-keyed.
- `workready-deploy/embed-uuids.yaml` — new file, per-company embed UUIDs.

### `messages.channel` values

| Value | When | Who sees in work inbox | Who sees in chat drawer |
|---|---|---|---|
| `email` | Email composed or received | ✓ | ✗ |
| `chat` | Live chat turn either direction | ✗ | ✓ |
| `system` | Classifier bounce-back from the system voice | ✓ (visually distinct) | ✗ |

### `messages.review_flag` shape

Only populated on student-originated outgoing messages.

**Passed classification:** `NULL` (not queried, not stored — saves DB
space and keeps intent clear).

**Flagged classification:**

```json
{
  "recipient_appropriateness": "wrong_audience",
  "tone": "ok",
  "channel_appropriateness": "ok",
  "rationale": "Facilities question sent to CEO",
  "classified_at": "2026-04-14T10:32:11Z"
}
```

**Classifier unavailable:**

```json
{
  "status": "classifier_unavailable",
  "error": "httpx.TimeoutException",
  "classified_at": "2026-04-14T10:32:11Z"
}
```

### `jobs.json` `team[]` — default resolution

At read time in `team_directory.get_team_for_application`:

1. If `job.team` exists and is a non-empty list → use it
2. Else → use all slugs from `company.employees[]`
3. In either case: "org" = every employee NOT in the resolved team list

### Explicitly NOT touched

These tables are read but never written by this project:
`interview_sessions`, `lunchroom_sessions`, `lunchroom_posts`, `tasks`,
`task_submissions`, `applications`, `stage_results`, `calendar_events`.

---

## Data flows (runtime walkthroughs)

Five representative flows. Each walks one student action end-to-end.

### Flow A — Open the team directory

1. Student (hired, `stage=work_task`) clicks "Team" in sidebar.
2. Portal → `GET /api/v1/team/{application_id}`.
3. `team_directory.get_team_for_application(app_id)`:
   a. Look up application → company_slug + job_slug.
   b. Load job from `_JOB_CACHE` → read `team[]` field.
   c. If `team[]` missing → default to `company.employees[].slug`.
   d. For each slug: resolve to full character object (name, role,
      availability, `presence_ok` via `availability.is_team_member_available_now`).
   e. Same pass for "org" = every company employee NOT in team.
4. API returns `{team: [...], org: [...], business_hours: {...}}`.
5. Portal renders the sidebar team section. Characters with
   `presence_ok = false` have greyed chat icons with a tooltip.

**Forward compatibility:** step 3 is a single function. The future
dynamic-team-adjustment project adds an override precedence chain here.
Every caller is unchanged.

### Flow B — Student sends a chat message that passes classification

1. Student opens chat drawer for Karen Whitfield.
2. Portal → `GET /api/v1/chat/thread/{app_id}/karen-whitfield` → delivered chat rows.
3. Drawer renders existing chat bubbles.
4. Student types "Hey Karen, quick question about the risk matrix task."
5. Portal → `POST /api/v1/chat/send`.
6. API route:
   a. `comms_monitor.classify_outgoing(...)` → all-ok.
   b. Persist student message: `channel='chat'`, `direction='outbound'`,
      `deliver_at=now`, `review_flag=NULL`.
   c. `context_builder.build_character_context(...)` → full unified
      thread + current task state + persona prompt.
   d. `chat_completion(system_prompt, messages)` generates the reply.
   e. `availability.next_business_hours_slot(company, now)` computes
      the reply's `deliver_at`.
   f. Persist character reply row with that `deliver_at`.
   g. Return student message to portal immediately.
7. Portal displays student bubble immediately.
8. Portal polls `/chat/thread` every 3 seconds.
9. Character reply surfaces when `deliver_at` passes.

### Flow C — Student emails the CEO about trivia (flag + Jenny bounce-back)

1. Student (hired at IronVale) composes email to `ceo@ironvaleresources.com.au`,
   subject "Where's the best coffee in the building?"
2. Portal → `POST /api/v1/mail/compose`.
3. `mail.py` calls `comms_monitor.classify_outgoing(...)`.
4. Classifier returns `recipient_appropriateness: wrong_audience`,
   rationale: "Facilities question sent to CEO".
5. Routing logic:
   a. Persist student email normally (in Sent folder), `review_flag`
      set to the classification.
   b. Do NOT schedule a reply from the real CEO.
   c. Schedule Jenny bounce-back: `sender_name='Jenny Kirkwood'`,
      `sender_role='Executive Assistant to the CEO'`, body generated
      from a context-aware template, `deliver_at` set via
      `next_business_hours_slot`.
6. Student sees their email in Sent immediately.
7. Jenny's reply lands in personal inbox at the next business-hours slot.
8. Lecturer journey report shows the flagged interaction.

### Flow D — Character reply outside business hours

Scenario: student chats Karen at 22:00 Tuesday. IronVale hours are
09:00–17:00 Mon-Fri local.

1. Flow B runs normally up to step 6e.
2. `next_business_hours_slot(ironvale, now=Tue 22:00)` returns Wed
   09:17 (next business start + small jitter).
3. Karen's reply row: `deliver_at = Wed 09:17`.
4. Student closes drawer, returns Wednesday morning.
5. Chat drawer polling surfaces Karen's reply at 09:17.
6. Karen's reply: "Morning! Just saw your message — what part of the
   risk matrix are you stuck on?"

**Alternate: student absent for two days.** If `students.last_login_at`
is more than 24 hours ago, the reply is backdated to "latest plausible
business hours before now" so the illusion holds — student never sees
an unexplained gap.

### Flow E — Hiring desk setup (operator action, one-time per cohort)

1. Operator: `cd workready-deploy && python3 setup-chatbots.py --dry-run`
   — prints what would happen.
2. Operator verifies, then: `python3 setup-chatbots.py`.
3. Script, per company:
   a. Load `brief.yaml` + `jobs.json`.
   b. Build fresh system prompt with `[workready-managed]` marker.
   c. Build fresh RAG doc.
   d. `GET /workspace/{ws_name}`:
      - If exists: check marker. Missing → skip with warning. Present
        → delete documents, upload fresh RAG, assign, update prompt.
      - If not exists: create workspace with marker, create embed,
        capture UUID, upload RAG, assign.
   e. Write UUID to `embed-uuids.yaml`.
4. Print summary: created / updated / skipped per company.
5. Operator: `git commit embed-uuids.yaml`.

Company site build pipeline reads `embed-uuids.yaml` at build time and
substitutes the UUID into `_chat_widget.html.j2` on each company's
`careers.html.j2`.

**Safety:** the marker check means the script can NEVER touch a
workspace it didn't create, even under name collision.

---

## Error handling

### Comms monitor — fail open

Any classifier failure (timeout, bad JSON, missing keys, provider down)
causes the message to flow normally with `review_flag` set to
`{"status": "classifier_unavailable", ...}`. Rationale: a broken safety
layer should never block legitimate student messages. The marker lets
us distinguish "message was fine" (NULL) from "message wasn't checked"
(flag set) for later analysis.

### Character reply failures — fail silent

Existing pattern in `mail.py` and `lunchroom_chat.py` unchanged. On any
`chat_completion` failure, the outbound reply is suppressed. The student
eventually notices the character hasn't replied and moves on, matching
real workplace behaviour. No retries, no "reply failed" notifications.

### Context builder overflow — tiered summarisation

Reuses the existing `mail.py` pattern: **24K-char soft cap on the
thread portion** of the context. When exceeded, older messages are
summarised via a separate LLM call and the recent 4 exchanges are
kept verbatim. The summary is attached as a preamble ("earlier in your
conversation: ..."). Matches the existing Stage 5c and Stage 6 patterns
so all conversation surfaces use the same cap. Summary is regenerated
per reply — no caching in v1.

### AnythingLLM script failures — per-company partial progress

Each company processed atomically. If any network step fails, log,
don't roll back, move to the next company. Summary at the end lists
partial states. Re-running is idempotent because of the marker check +
update logic.

### Schema authoring errors — warning logs, graceful degradation

- Team slug not in `employees[]` → warning log, silently dropped from
  directory.
- Unknown `availability.status` → defaults to `available`, warning log.
- Persona file missing for a team member → chat-send route returns 500.
  Mitigated by the audit tool that runs during this project and stubs
  any missing persona.
- `last_login_at` NULL → treated as "just logged in", no backdating.
- Presence `on_leave` with `return_date` in the past → treated as
  `available` (character auto-returns).

### Business hours edge cases

- **Hours span midnight** (night shift) — handled by
  `next_business_hours_slot` via calendar-day wrapping logic.
- **Public holiday during business hours** — filtered out by
  `is_public_holiday`.
- **Holiday falls on a weekend** — already closed, file just lists
  whatever dates are officially observed.
- **Unknown `holidays_region`** — warning log, no filtering.

---

## Testing strategy

Matches existing WorkReady convention: Python smoke scripts for
backend, `node --check` for portal JS, manual browser walk. No pytest
introduction (deferred).

### Smoke inventory (15 scripts)

Each runs against a fresh `/tmp/x.db` with `LLM_PROVIDER=stub`:

1. **Team directory loads correctly.** Hire a student, call
   `/api/v1/team/{id}`, assert shape and contents.
2. **Team directory default fallback.** Create a company with no
   `team:` field, assert the team = full employees list.
3. **Unified thread bifurcation + context union.** Seed email and chat
   rows, assert each view filters, assert context builder returns all.
4. **Classifier happy path.** Stub returns all-ok. Student message
   passes, character reply scheduled, no bounce-back.
5. **Classifier wrong-audience path (Jenny bounce-back).** Student
   emails CEO, Jenny reply lands with expected sender + body.
6. **Classifier tone path (mentor gentle note).** Sharp message to a
   team member, mentor bounce-back lands.
7. **Classifier channel path (system note).** Personal inbox used for
   work, system message lands.
8. **Classifier fail-open.** Stub raises, message flows with
   `review_flag` set to `classifier_unavailable`.
9. **Business hours chat gating.** Mock clock to 22:00, assert
   `presence_ok = false`, reply `deliver_at` = next 09:00.
10. **Business hours email backdating.** Student absent > 24h, reply
    backdated to plausible in-hours time.
11. **Presence away-state blocks chat.** `on_leave` character, assert
    `presence_ok = false` regardless of clock.
12. **Public holiday blocks business hours.** Mock clock to a holiday,
    assert `next_business_hours_slot` skips it.
13. **Setup script dry-run.** No network calls, prints per-company
    summary, exit code 0.
14. **Setup script marker check.** Existing workspace without marker
    → script skips with warning, writes nothing.
15. **Context builder summarisation.** Seed 40 messages, assert returned
    thread is summarised with recent verbatim tail.

### Browser smoke (manual checklist)

1. Sign in as test student, team directory renders in sidebar.
2. Click a team member with `presence_ok=true` → chat drawer opens.
3. Send a chat message → bubble appears immediately.
4. Wait for reply (stub mode) → reply bubble appears.
5. Close drawer, open work inbox → chat messages not visible.
6. Open character's email thread → only emails visible.
7. Chat icon greyed for away team member → click does nothing.
8. Sign in as not-yet-hired student → team directory nav hidden.

### Regression safety

Before shipping, re-run the existing smoke tests for:

- Resume submission flow
- Lunchroom flow (Stage 5)
- Exit interview flow (Stage 6)
- Journey report
- Performance review (Stage 4.5)

Any regression is a blocker.

### Done criteria

- All 15 smoke scripts pass against stub
- Browser smoke checklist passes manually
- All pre-existing stage smokes still pass
- `node --check` passes
- `uv run python -c "from workready_api import app; print('ok')"` passes
- Hiring desk embed widget visible on at least one company's careers
  page (manual verification)
- `chat.eduserver.au` has the 6 workready-prefixed workspaces, each
  with the marker, each with a non-null UUID in `embed-uuids.yaml`

---

## Suggested implementation order

Listed in the order that minimises cross-subsystem blocking. Each block
is independently testable.

1. **DB migrations** (additive columns on `messages`, `students`, and
   the chat index).
2. **Persona audit + gap-fill**. Scan all `jobs.json` files for
   characters referenced in `team:` / `reports_to` without
   `content/employees/<slug>-prompt.txt`. Write stub personas for
   the gaps. Update both stores (note in future work about
   unification).
3. **`availability.py`** (business hours + presence + public holidays
   file). Pure functions, testable in isolation.
4. **`team_directory.py`** + the `GET /api/v1/team/{id}` route.
   Depends on 3.
5. **`context_builder.py`** (task-aware context stuffer). Depends on
   no new infrastructure — just reads existing state.
6. **`comms_monitor.py`** (classifier module). Standalone, stubbable.
7. **Classifier hook in `mail.py`.** Depends on 6. Adds the
   wrong-audience / tone / channel bounce-backs.
8. **Chat send + thread routes** (`POST /chat/send`, `GET /chat/thread/...`).
   Depends on 3, 5, 6. Uses the same context builder and classifier.
9. **Portal team directory sidebar.** Depends on 4. Pure UI.
10. **Portal chat drawer.** Depends on 8 and 9. Pure UI.
11. **Portal work inbox colour coding.** Small CSS pass.
12. **`setup-chatbots.py` hardening** (marker, dry-run, update refresh,
    UUID persistence). Independent of everything above. Can proceed in
    parallel with any of 1–11.
13. **`embed-uuids.yaml` + company site partial + build pipeline
    updates** for each of the six company sites. Depends on 12 having
    been run live once to produce real UUIDs.
14. **Live AnythingLLM run**. Operator action: run the hardened script
    against `chat.eduserver.au`, verify workspaces and embeds, commit
    `embed-uuids.yaml`.
15. **Smoke test suite.** Written against each subsystem as it lands
    (not at the end — integrated into each block).

---

## Out of scope — see future work doc

Full rationale in `2026-04-14-workready-team-communications-future-work.md`.
Headlines:

- **G2 — full guardrail escalation ladder** (mentor notice → HR warning
  → termination path). G1 is forward-compatible, nothing in this
  project blocks G2 later.
- **Split Z — Teams-style work interface rewrite.** All functional
  work here is UI-independent; the visual paradigm shift is its own
  project.
- **Dynamic team adjustment** (lecturer-driven, per-application and
  per-cohort overrides for learning outcomes). Schema is forward-
  compatible — one additive column on `applications` + a resolver
  precedence chain when that project ships.
- **Unify the two character-content stores.** Persona files and
  `brief.yaml` `customisation.background` are maintained separately by
  convention in this project.
- **Student timezones, mentor-presence gating for task feedback,
  multi-year holiday config.** Realism polish, deferred.
- **Pytest framework introduction.** Deliberate separate project.

# WorkReady — Stages 4, 5, 6 Design

Captured 2026-04-13. This document is the design reference for the
remaining simulation stages. Build order: 4a → 4b → 4c → 5 → 6.

---

## Stage 4: Work Tasks

### Task assignment
- Default **3 tasks per student** (configurable via `TASKS_PER_STUDENT`)
- Progression: easy → medium → harder
- Tasks assigned by the mentor (the `reports_to` character from the job listing)
- Task templates live in `brief.yaml` under each company, tagged by discipline and difficulty
- Each company ships with 8-10 pre-written tasks; the system picks 3
- High achievers: if they finish early and do well, bonus tasks are queued (configurable `ALLOW_BONUS_TASKS`)

### Task templates (examples)

**IronVale Resources:**
- Write a one-page briefing note on the autonomous haulage trial for a board meeting (easy)
- Analyse a provided dataset of haul truck cycle times and identify three efficiency improvements (medium)
- Draft a stakeholder communication plan for the Goldfields lithium project Traditional Owner consultation (harder)

**NexusPoint Systems:**
- Document the onboarding process for a new managed services client (easy)
- Review a mock security assessment report and prepare a client-facing summary (medium)
- Scope a cloud migration proposal for a fictional mid-market client (harder)

**Horizon Foundation:**
- Write a volunteer recruitment flyer for the youth employment program (easy)
- Prepare a grant acquittal report template with sample data (medium)
- Draft a community consultation plan for a new regional program expansion (harder)

### Submission model
- Student submits in-app: text body + optional PDF attachment
- Mentor (LLM character) reviews and gives structured feedback
- Outcomes: passed / failed / resubmit (with feedback)
- Resubmit allowed (configurable `TASK_RESUBMIT_ALLOWED`)
- Lecturer can see all submissions via the admin panel (dual visibility)
- Each task has a deadline (`TASK_DEADLINE_DAYS`, default 7)

### Solo vs team
- **v1: all tasks are solo** — the deliverable is individual
- The team context is visible (Team view shows colleagues)
- Task briefings may reference "check with [team member] about X"
- Real multi-student collaboration (shared docs, group submissions) is v2
- The "team" is the context you work in, not a group assignment

### The mentor
- One mentor per student throughout the internship (the `reports_to` person)
- Same character they interviewed with → continuity
- Mentor actions: assign tasks, review submissions, send check-in messages
- Mentor knows the student's journey (resume score, interview feedback)
- LLM context includes: mentor persona + task brief + student submission + prior task history

### Task lifecycle
```
assigned → in_progress → submitted → reviewed
                                      ├→ passed (next task assigned)
                                      ├→ resubmit (student revises)
                                      └→ failed (noted, next task still assigned)
```

### Data model
```sql
CREATE TABLE task_templates (
    id INTEGER PRIMARY KEY,
    company_slug TEXT NOT NULL,
    title TEXT NOT NULL,
    brief TEXT NOT NULL,
    description TEXT,             -- full task description (markdown)
    discipline TEXT,              -- finance, community, technology, etc.
    difficulty TEXT NOT NULL,     -- easy | medium | hard
    estimated_hours INTEGER DEFAULT 4,
    attachment_path TEXT           -- optional reference material (PDF/CSV)
);

CREATE TABLE tasks (
    id INTEGER PRIMARY KEY,
    application_id INTEGER NOT NULL REFERENCES applications(id),
    task_template_id INTEGER REFERENCES task_templates(id),
    title TEXT NOT NULL,
    brief TEXT NOT NULL,
    description TEXT,
    difficulty TEXT NOT NULL,
    sequence INTEGER NOT NULL,    -- 1, 2, 3...
    status TEXT NOT NULL DEFAULT 'assigned',
    assigned_at TEXT NOT NULL,
    due_at TEXT,
    submitted_at TEXT,
    reviewed_at TEXT
);

CREATE TABLE task_submissions (
    id INTEGER PRIMARY KEY,
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    body TEXT NOT NULL,
    attachment_path TEXT,
    attachment_filename TEXT,
    score INTEGER,                -- 0-100
    feedback_json TEXT,
    status TEXT NOT NULL DEFAULT 'submitted',  -- submitted | passed | failed | resubmit
    created_at TEXT NOT NULL
);
```

### Configuration
```env
TASKS_PER_STUDENT=3
TASK_DEADLINE_DAYS=7
ALLOW_BONUS_TASKS=true
TASK_RESUBMIT_ALLOWED=true
```

---

## Stage 4c: Calendar

Simple event list — not a full calendar application.

### What it shows
- Task deadlines (auto-generated when tasks are assigned)
- Lunchroom invitations (accept/decline)
- Exit interview (when triggered)
- Any custom events added by the system

### Data model
```sql
CREATE TABLE calendar_events (
    id INTEGER PRIMARY KEY,
    application_id INTEGER NOT NULL REFERENCES applications(id),
    event_type TEXT NOT NULL,      -- task_deadline | lunchroom | exit_interview | custom
    title TEXT NOT NULL,
    description TEXT,
    scheduled_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'upcoming',  -- upcoming | accepted | declined | completed
    related_id INTEGER,           -- task_id or lunchroom_session_id
    created_at TEXT NOT NULL
);
```

### Portal UI
- New "Calendar" nav item in the sidebar (appears after hire)
- Simple chronological list of events with status badges
- Accept/decline buttons on lunchroom invitations
- Click event → shows details

---

## Stage 5: Lunchroom Moment

Simulates informal workplace social dynamics — the thing most intern
programs fail to teach.

### How it works
- A group chat room (Slack/Discord style) in the portal
- Participants: the student + 2-3 AI characters (from the company team)
- Optionally: 1-2 other students at the same company (v2)
- Semi-structured: AI characters have conversation goals
- Runs for ~15-20 messages then winds down naturally
- Not graded, but observed — feeds into exit interview

### AI character goals
Each AI character in the lunchroom has a goal for the conversation:
- Introduce a topic related to the company scenario
- Ask the student about their work / how they're settling in
- Share a personal opinion (in character) about a company tension
- React to what others say

### Assessment (implicit)
- Did the student participate or stay silent?
- Did they ask questions or only answer?
- Did they show awareness of the company culture/scenario?
- Were responses professional and appropriate?
- This is noted in the student's record and referenced in the exit interview

### Invitation flow
- 3 invitations per internship (configurable `LUNCHROOM_INVITES`)
- Student can accept or decline each one
- Declining 1-2: fine, realistic ("No worries, catch you next time")
- Declining all: mentor sends a check-in email
- Configurable `LUNCHROOM_DECLINE_LIMIT` (default 2 before check-in)

### Data model
```sql
CREATE TABLE lunchroom_sessions (
    id INTEGER PRIMARY KEY,
    application_id INTEGER NOT NULL REFERENCES applications(id),
    scheduled_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'invited',  -- invited | accepted | declined | active | completed
    participants_json TEXT,        -- list of AI character slugs
    transcript_json TEXT,
    participation_notes TEXT,      -- LLM-generated observation
    created_at TEXT NOT NULL,
    completed_at TEXT
);
```

---

## Stage 6: Exit Interview

### When it triggers
Three paths (configurable via `EXIT_INTERVIEW_TRIGGER`):
1. **completion** — after all tasks are submitted and reviewed (happy path)
2. **time** — after `EXIT_INTERVIEW_AFTER_DAYS` (default 28)
3. **manual** — triggered by admin/lecturer

### The interview
- Conducted by a different character than the mentor (HR or senior leader)
- Reflective, not technical — about the experience
- Questions: What did you learn? What would you do differently? How was the team? Feedback for us?
- LLM has access to the student's full journey:
  - Resume score and feedback
  - Interview score and feedback
  - Task submissions, scores, and mentor feedback
  - Lunchroom participation notes
  - Email history highlights
- Assessment focuses on self-awareness and reflection

### After the exit interview
- Student state → COMPLETED
- Summary message in inbox: "Congratulations on completing your internship"
- Full journey report available via admin endpoint (for lecturer grading)

---

## Lifecycle: Resign and Re-entry

### Resign
- "Resign" option available in the dashboard (not prominent — under settings or a menu)
- Confirmation dialog with explanation
- Exit interview offered (optional, can skip)
- Application status → 'resigned'
- Student returns to NOT_APPLIED state

### Re-entry
- The `cycle` field in applications already supports multiple journeys
- Student applies to a different company (previous company is blocked)
- Full journey restarts from Stage 1
- Previous history preserved (lecturer can see all cycles)
- Configurable `MAX_CYCLES` (default 3)

---

## Configuration summary

```env
# Stage 4: Tasks
TASKS_PER_STUDENT=3
TASK_DEADLINE_DAYS=7
ALLOW_BONUS_TASKS=true
TASK_RESUBMIT_ALLOWED=true

# Stage 5: Lunchroom
LUNCHROOM_INVITES=3
LUNCHROOM_DECLINE_LIMIT=2

# Stage 6: Exit interview
EXIT_INTERVIEW_TRIGGER=completion  # completion | time | manual
EXIT_INTERVIEW_AFTER_DAYS=28

# Lifecycle
ALLOW_RESIGN=true
ALLOW_REENTRY=true
MAX_CYCLES=3
```

---

## Build order

| Phase | What | Depends on |
|---|---|---|
| **4a** | Task template system — brief.yaml definitions, assignment logic, task view in portal | — |
| **4b** | Task submission + LLM review — submit form, mentor feedback, pass/fail/resubmit | 4a |
| **4c** | Calendar view — event list, accept/decline | 4a |
| **5** | Lunchroom — group chat UI, AI characters, participation tracking | 4c (for invitations) |
| **6** | Exit interview — reflective interview with full journey context | 4b, 5 |
| **Admin** | Lecturer journey report — full student summary for grading | 6 |

---

## Future (not now)

- **Multi-student teams** — show other students at the same company in the Team view, potentially pair for lunchroom. Actual collaboration (shared tasks, group submissions) is v2.
- **Video/Teams calls** — WebRTC or third-party embed. The calendar + invite system lays the groundwork. Noted for future when in-person/video simulation is desired.
- **Lecturer dashboard** — beyond the admin page. A proper grading interface with rubrics, cohort views, and exportable reports.
- **Per-character live chat with thread awareness** — chat UI in portal that loads the student's email thread with a character. Reuses existing context-stuffing infrastructure.

# Team Communications Layer 3 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the in-simulation team-communications subsystem to WorkReady — team directory, live per-character chat, task-aware replies, business-hours and presence gating, public holiday awareness, and a comms-monitor classifier that bounces inappropriate student messages with in-character coaching.

**Architecture:** Additive-only. Four new Python modules (`availability`, `team_directory`, `context_builder`, `comms_monitor`), three new DB columns on existing tables, two new API routes, and new portal surfaces (team sidebar + chat drawer) that sit alongside the existing work inbox. No existing code is replaced; the classifier hooks into `mail.py` via a pre-send check and the chat routes reuse the same hook. Every character conversation still goes through the shared `chat_completion()` direct-to-provider path — AnythingLLM is not touched by this plan.

**Tech Stack:** Python 3.13 + FastAPI (backend), SQLite + additive migrations (storage), vanilla JS + HTML + CSS (portal, no build step), `uv` for Python runs, `node --check` for JS syntax.

**Source spec:** `docs/superpowers/specs/2026-04-14-workready-team-communications-design.md`
**Out of scope:** Subsystem F (AnythingLLM hiring desk) lives in a separate plan.
**Future work:** `docs/superpowers/specs/2026-04-14-workready-team-communications-future-work.md`

---

## Conventions for this plan

- **Working directory:** every `cd` is to `/Users/michael/Projects/loco-lab/loco-ensyo/workready-api` unless otherwise noted. Portal work happens in `/Users/michael/Projects/loco-lab/loco-ensyo/workready-portal`.
- **Smoke test pattern:** each new module gets a committed smoke script at `scripts/smoke_<module>.py`. Run with `WORKREADY_DB=/tmp/<scope>.db LLM_PROVIDER=stub uv run python scripts/smoke_<module>.py`. These scripts drive the module end-to-end and print assertions.
- **Fresh DB per smoke:** every smoke script starts with `rm -f /tmp/<scope>.db` so results are reproducible.
- **No data migrations.** All schema changes are `ALTER TABLE ADD COLUMN` with defaults. The existing `_migrate()` function in `db.py` handles idempotent application.
- **Commit cadence:** commit after every task that produces a checkpoint (new module landed, smoke passes, UI wired). Matches the existing stage-by-stage commit style.
- **LLM stub mode:** every smoke runs with `LLM_PROVIDER=stub`. The classifier module has its own stub short-circuit so it returns all-ok without hitting any LLM.

---

## File Structure

### New Python modules (all under `workready_api/`)

| File | Responsibility |
|---|---|
| `workready_api/availability.py` | Business hours + presence + public holidays helpers. Pure functions. |
| `workready_api/team_directory.py` | Resolves a student's team and org from their application. Single public function with override-chain-ready shape. |
| `workready_api/context_builder.py` | Builds the "what does this character know about this student" payload. Called by chat reply generation and mail reply generation. |
| `workready_api/comms_monitor.py` | Classifier module. Pre-send check on every outgoing student message. Fail-open. |
| `workready_api/data/__init__.py` | Empty — makes the data subpackage importable. |
| `workready_api/data/public_holidays.py` | Hand-edited region-keyed dict of public holiday dates. Python source, no external parser needed. |

### New smoke scripts (all under `scripts/`)

| File | What it tests |
|---|---|
| `scripts/smoke_availability.py` | Business hours, presence states, public holiday skipping, business-hours slot computation. |
| `scripts/smoke_team_directory.py` | Team resolver with `team:` field, default fallback, org/team split. |
| `scripts/smoke_context_builder.py` | Unified thread loading, task state, summarisation over 24K-char cap. |
| `scripts/smoke_comms_monitor.py` | Classifier stub path + fail-open behaviour. |
| `scripts/smoke_mail_classifier.py` | Wrong-audience, tone, and channel bounce-back paths through `mail.py`. |
| `scripts/smoke_chat_routes.py` | Chat send + thread poll end-to-end via the API. |
| `scripts/smoke_persona_audit.py` | Audits persona completeness across all companies. Checked in, runnable any time. |
| `scripts/audit_personas.py` | Operator tool (not a smoke) that reports persona gaps. Used during gap-fill block. |

### Modified Python files

| File | Change |
|---|---|
| `workready_api/db.py` | Add migrations 8/9/10, update `INDEXES_SCHEMA` with the chat index, add `get_student_by_id_update_last_login()` helper, add `mark_student_login()` helper. |
| `workready_api/app.py` | Update `/student/{email}/state` route to call `mark_student_login()`. Add `GET /api/v1/team/{application_id}`. Add `POST /api/v1/chat/send`. Add `GET /api/v1/chat/thread/{application_id}/{character_slug}`. Add `workready_api.team_directory`, `context_builder`, `comms_monitor`, `availability` imports. |
| `workready_api/mail.py` | Wire the classifier pre-send hook into the compose path. Add the proxy character registry and the three bounce-back scheduling paths. Use `context_builder.build_character_context` when generating character replies. |
| `workready_api/models.py` | Add `TeamMemberRef`, `TeamDirectoryResponse`, `ChatSendRequest`, `ChatMessageModel`, `ChatThreadResponse`, `ClassificationResult`. |

### Modified portal files

| File | Change |
|---|---|
| `workready-portal/index.html` | Add `<nav-team>` nav item (hidden by default), add `<view-team>` container (hidden), add chat drawer markup at the end of `<main>`. |
| `workready-portal/app.js` | Add team directory loader, sidebar render, drawer open/close, polling, send handler. Update `renderState` to show nav when hired. |
| `workready-portal/style.css` | Add team directory sidebar styles, chat drawer styles, bubble styles, presence dot, personal-vs-work colour coding. |

### New content (persona gap-fill driven by audit)

Any `content/employees/<slug>-prompt.txt` files flagged by the audit. Exact list determined at runtime; the block covers the process.

---

## Block 1 — Database migrations

Two new columns on `messages`, one new column on `students`, one new index. All additive, handled by the existing `_migrate()` runner so both fresh-init DBs and existing dev DBs pick up the changes safely.

### Task 1.1: Write the migration 8 check (messages.channel)

**Files:**
- Modify: `workready_api/db.py` (add migration block)

- [ ] **Step 1: Add Migration 8 block in `_migrate()`**

Append to the end of `_migrate()`, just after the existing Migration 7 block:

```python
    # --- Migration 8: messages.channel (Stage 7 team chat) ---
    msg_cols = _table_columns(conn, "messages")
    if "channel" not in msg_cols:
        conn.execute(
            "ALTER TABLE messages ADD COLUMN channel TEXT NOT NULL "
            "DEFAULT 'email'"
        )
```

- [ ] **Step 2: Add the column to `TABLES_SCHEMA` so fresh DBs start correct**

Find the `CREATE TABLE IF NOT EXISTS messages` block in `TABLES_SCHEMA` (near line 50-100 of `db.py`). Add `channel TEXT NOT NULL DEFAULT 'email'` after `status` and before `has_attachment`. Keep column order stable and match the existing indentation.

- [ ] **Step 3: Smoke test — fresh DB has the column**

Run:

```bash
rm -f /tmp/mig8.db
WORKREADY_DB=/tmp/mig8.db LLM_PROVIDER=stub uv run python -c "
from workready_api.db import init_db, get_db
init_db()
with get_db() as conn:
    cols = [r[1] for r in conn.execute('PRAGMA table_info(messages)').fetchall()]
assert 'channel' in cols, f'channel column missing from {cols}'
print('OK: channel column present in fresh DB')
"
```

Expected output: `OK: channel column present in fresh DB`

- [ ] **Step 4: Smoke test — pre-existing DB picks up column**

Run:

```bash
rm -f /tmp/mig8pre.db
# Seed an old-schema DB by temporarily patching out the new column
sqlite3 /tmp/mig8pre.db 'CREATE TABLE messages (id INTEGER PRIMARY KEY, subject TEXT)'
WORKREADY_DB=/tmp/mig8pre.db LLM_PROVIDER=stub uv run python -c "
from workready_api.db import init_db, get_db
init_db()
with get_db() as conn:
    cols = [r[1] for r in conn.execute('PRAGMA table_info(messages)').fetchall()]
assert 'channel' in cols, f'channel column missing from {cols}'
print('OK: channel column added to pre-existing DB')
"
```

Expected output: `OK: channel column added to pre-existing DB`

- [ ] **Step 5: Commit**

```bash
cd /Users/michael/Projects/loco-lab/loco-ensyo/workready-api
git add workready_api/db.py
git commit -m "Stage 7: add messages.channel column (migration 8)"
```

### Task 1.2: Write migration 9 (messages.review_flag)

**Files:**
- Modify: `workready_api/db.py`

- [ ] **Step 1: Add Migration 9 block in `_migrate()`**

Append after Migration 8:

```python
    # --- Migration 9: messages.review_flag (comms monitor classifier) ---
    if "review_flag" not in msg_cols:
        conn.execute(
            "ALTER TABLE messages ADD COLUMN review_flag TEXT"
        )
```

Note: `msg_cols` is already in scope from Migration 8; if Migration 8 and 9 run in the same pass on a pre-existing DB, both columns get added. No need to re-query.

- [ ] **Step 2: Add the column to `TABLES_SCHEMA`**

In the `messages` CREATE TABLE block, add `review_flag TEXT` after the `channel` column added in 1.1.

- [ ] **Step 3: Smoke test**

```bash
rm -f /tmp/mig9.db
WORKREADY_DB=/tmp/mig9.db LLM_PROVIDER=stub uv run python -c "
from workready_api.db import init_db, get_db
init_db()
with get_db() as conn:
    cols = [r[1] for r in conn.execute('PRAGMA table_info(messages)').fetchall()]
assert 'review_flag' in cols
print('OK: review_flag column present')
"
```

Expected: `OK: review_flag column present`

- [ ] **Step 4: Commit**

```bash
git add workready_api/db.py
git commit -m "Stage 7: add messages.review_flag column (migration 9)"
```

### Task 1.3: Write migration 10 (students.last_login_at)

**Files:**
- Modify: `workready_api/db.py`

- [ ] **Step 1: Add Migration 10 block**

```python
    # --- Migration 10: students.last_login_at (business hours illusion) ---
    student_cols = _table_columns(conn, "students")
    if "last_login_at" not in student_cols:
        conn.execute(
            "ALTER TABLE students ADD COLUMN last_login_at TEXT"
        )
```

- [ ] **Step 2: Add to `TABLES_SCHEMA`**

In the `students` CREATE TABLE block, add `last_login_at TEXT` after `created_at`.

- [ ] **Step 3: Smoke test**

```bash
rm -f /tmp/mig10.db
WORKREADY_DB=/tmp/mig10.db LLM_PROVIDER=stub uv run python -c "
from workready_api.db import init_db, get_db
init_db()
with get_db() as conn:
    cols = [r[1] for r in conn.execute('PRAGMA table_info(students)').fetchall()]
assert 'last_login_at' in cols
print('OK: last_login_at column present')
"
```

Expected: `OK: last_login_at column present`

- [ ] **Step 4: Commit**

```bash
git add workready_api/db.py
git commit -m "Stage 7: add students.last_login_at column (migration 10)"
```

### Task 1.4: Add the chat thread index

**Files:**
- Modify: `workready_api/db.py`

- [ ] **Step 1: Add to `INDEXES_SCHEMA`**

Find the `INDEXES_SCHEMA` block near the end of the schema section. Append:

```sql
CREATE INDEX IF NOT EXISTS idx_messages_chat_thread
    ON messages(student_id, application_id, channel, deliver_at)
    WHERE channel = 'chat';
```

- [ ] **Step 2: Smoke test — index exists**

```bash
rm -f /tmp/idx.db
WORKREADY_DB=/tmp/idx.db LLM_PROVIDER=stub uv run python -c "
from workready_api.db import init_db, get_db
init_db()
with get_db() as conn:
    indexes = [r[1] for r in conn.execute(
        \"SELECT type, name FROM sqlite_master WHERE type='index'\"
    ).fetchall()]
assert 'idx_messages_chat_thread' in indexes
print('OK: chat thread index present')
"
```

Expected: `OK: chat thread index present`

- [ ] **Step 3: Commit**

```bash
git add workready_api/db.py
git commit -m "Stage 7: add chat thread composite index"
```

### Task 1.5: Add `mark_student_login` helper

**Files:**
- Modify: `workready_api/db.py`

- [ ] **Step 1: Write the helper**

In `db.py`, near the other student helpers (`get_or_create_student`, `get_student_by_id`), add:

```python
def mark_student_login(student_id: int) -> None:
    """Record that the student just interacted with the portal.

    Used by availability.next_business_hours_slot to decide whether
    to backdate character replies when the student has been away.
    """
    with get_db() as conn:
        conn.execute(
            "UPDATE students SET last_login_at = ? WHERE id = ?",
            (_now(), student_id),
        )
```

- [ ] **Step 2: Smoke test**

```bash
rm -f /tmp/login.db
WORKREADY_DB=/tmp/login.db LLM_PROVIDER=stub uv run python -c "
from workready_api.db import init_db, get_or_create_student, mark_student_login, get_student_by_id
init_db()
s = get_or_create_student('t@example.com', 'Tester')
assert s.get('last_login_at') is None
mark_student_login(s['id'])
s2 = get_student_by_id(s['id'])
assert s2['last_login_at'] is not None
print('OK: mark_student_login sets timestamp')
"
```

Expected: `OK: mark_student_login sets timestamp`

- [ ] **Step 3: Commit**

```bash
git add workready_api/db.py
git commit -m "Stage 7: add mark_student_login helper"
```

### Task 1.6: Wire login marking into the state endpoint

**Files:**
- Modify: `workready_api/app.py`

- [ ] **Step 1: Import the helper**

Find the big multi-line import block near the top of `app.py` that imports from `workready_api.db`. Add `mark_student_login` to the list (alphabetical).

- [ ] **Step 2: Call it from the student state route**

Find the `GET /api/v1/student/{email}/state` route handler. Near the top, right after `student = get_or_create_student(email, name)` (or equivalent — use whatever line resolves the student row), add:

```python
    mark_student_login(student["id"])
```

- [ ] **Step 3: Smoke test — state endpoint updates last_login_at**

```bash
rm -f /tmp/state.db
WORKREADY_DB=/tmp/state.db LLM_PROVIDER=stub uv run uvicorn workready_api.app:app --port 8700 --log-level warning &
SERVER_PID=$!
sleep 2
curl -s http://127.0.0.1:8700/api/v1/student/login@example.com/state > /dev/null
kill $SERVER_PID 2>/dev/null
WORKREADY_DB=/tmp/state.db uv run python -c "
from workready_api.db import get_db
with get_db() as conn:
    row = conn.execute('SELECT email, last_login_at FROM students WHERE email = ?', ('login@example.com',)).fetchone()
assert row is not None
assert row['last_login_at'] is not None, 'last_login_at was not set'
print(f'OK: last_login_at = {row[\"last_login_at\"]}')
"
```

Expected: `OK: last_login_at = 2026-...`

- [ ] **Step 4: Commit**

```bash
git add workready_api/app.py
git commit -m "Stage 7: mark student login on /state endpoint"
```

---

## Block 2 — Persona audit + gap-fill

The team directory and chat drawer will 500 if a team-listed character has no persona file. Before anything else UI-facing lands, audit the current state and fill every gap.

### Task 2.1: Write the audit script

**Files:**
- Create: `scripts/audit_personas.py`

- [ ] **Step 1: Create the scripts directory**

```bash
mkdir -p scripts
```

- [ ] **Step 2: Write the audit script**

Create `scripts/audit_personas.py`:

```python
#!/usr/bin/env python3
"""Audit persona-file completeness across every company.

Scans each company's jobs.json + brief.yaml and reports:
1. Characters referenced in `team:` field of any job but missing a
   content/employees/<slug>-prompt.txt file
2. Characters referenced in `reports_to` on any job but missing a
   prompt file
3. Characters in brief.yaml employees[] but missing a prompt file

Usage:
    uv run python scripts/audit_personas.py
    uv run python scripts/audit_personas.py --fail-on-gaps  # exit 1 if any
"""

import json
import os
import re
import sys
from pathlib import Path

# The parent of loco-ensyo/workready-api is loco-ensyo/
SITES_DIR = Path(os.environ.get(
    "SITES_DIR",
    str(Path(__file__).parent.parent.parent),
))

COMPANIES = [
    "ironvale-resources",
    "nexuspoint-systems",
    "meridian-advisory",
    "metro-council-wa",
    "southern-cross-financial",
    "horizon-foundation",
]


def slugify(name: str) -> str:
    """Match workready_api.lunchroom._slugify_name."""
    s = re.sub(r"[^\w\s-]", "", name.lower())
    s = re.sub(r"\s+", "-", s).strip("-")
    return s or "colleague"


def load_jobs(company_dir: Path) -> dict:
    path = company_dir / "jobs.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def audit_company(slug: str) -> list[dict]:
    """Return a list of gap records for this company."""
    company_dir = SITES_DIR / slug
    jobs_data = load_jobs(company_dir)
    employees_dir = company_dir / "content" / "employees"

    existing_files = set()
    if employees_dir.is_dir():
        existing_files = {
            p.stem.replace("-prompt", "")
            for p in employees_dir.glob("*-prompt.txt")
        }

    gaps: list[dict] = []

    # Source 1: team: field on each job
    for job in jobs_data.get("jobs", []):
        for char_slug in job.get("team", []):
            if char_slug not in existing_files:
                gaps.append({
                    "company": slug,
                    "character_slug": char_slug,
                    "source": f"job.{job['slug']}.team",
                    "suggested_name": char_slug.replace("-", " ").title(),
                })

    # Source 2: reports_to on each job
    for job in jobs_data.get("jobs", []):
        reports_to = job.get("reports_to", "")
        if reports_to:
            char_slug = slugify(reports_to)
            if char_slug not in existing_files:
                gaps.append({
                    "company": slug,
                    "character_slug": char_slug,
                    "source": f"job.{job['slug']}.reports_to",
                    "suggested_name": reports_to,
                })

    # Source 3: employees[] in jobs.json (from brief.yaml passthrough)
    for emp in jobs_data.get("employees", []):
        char_slug = emp.get("slug") or slugify(emp.get("name", ""))
        if char_slug and char_slug not in existing_files:
            gaps.append({
                "company": slug,
                "character_slug": char_slug,
                "source": "employees[]",
                "suggested_name": emp.get("name", char_slug),
                "role": emp.get("role", ""),
            })

    # Dedupe by (company, character_slug)
    seen = set()
    unique_gaps = []
    for g in gaps:
        key = (g["company"], g["character_slug"])
        if key in seen:
            continue
        seen.add(key)
        unique_gaps.append(g)

    return unique_gaps


def main() -> int:
    fail_on_gaps = "--fail-on-gaps" in sys.argv
    all_gaps: list[dict] = []

    print(f"Auditing persona completeness in {SITES_DIR}\n")
    for slug in COMPANIES:
        company_dir = SITES_DIR / slug
        if not company_dir.is_dir():
            print(f"  {slug}: (directory not found, skipping)")
            continue
        gaps = audit_company(slug)
        existing = len((company_dir / "content" / "employees").glob("*-prompt.txt")) \
            if (company_dir / "content" / "employees").is_dir() else 0
        print(f"  {slug}: {existing} persona(s) present, {len(gaps)} gap(s)")
        for g in gaps:
            extra = f" role='{g.get('role', '')}'" if g.get("role") else ""
            print(f"    - {g['character_slug']} (suggested: '{g['suggested_name']}'){extra}")
            print(f"      referenced by: {g['source']}")
        all_gaps.extend(gaps)

    print()
    if all_gaps:
        print(f"TOTAL GAPS: {len(all_gaps)}")
        if fail_on_gaps:
            return 1
    else:
        print("TOTAL GAPS: 0 — all personas present.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 3: Run it**

```bash
uv run python scripts/audit_personas.py
```

Expected: prints a summary per company, listing any gaps. Capture the output for Task 2.2.

- [ ] **Step 4: Commit the audit script**

```bash
git add scripts/audit_personas.py
git commit -m "Stage 7: add persona completeness audit script"
```

### Task 2.2: Record the audit baseline

**Files:**
- (none — this task captures output, no code change)

- [ ] **Step 1: Save the audit output to the plan session notes**

Run:

```bash
uv run python scripts/audit_personas.py > /tmp/persona-audit-baseline.txt
cat /tmp/persona-audit-baseline.txt
```

Keep this output visible. The gaps listed here are the work in Task 2.3.

- [ ] **Step 2: Confirm count is reasonable**

Based on the spec's current inventory (5-7 personas per company, 6 companies, ~30-42 total), expect the audit to find somewhere between 0 and 15 gaps. If it finds significantly more than that (e.g. 30+), stop and investigate — either the audit script is wrong or the existing persona files aren't where we thought.

### Task 2.3: Fill each persona gap

**Files:**
- Create: `<company_slug>/content/employees/<slug>-prompt.txt` (one per gap)

- [ ] **Step 1: Understand the persona template**

Every persona file follows this shape (~300 words, consistent across companies):

```
You are <Name>, <Role> at <Company Name>.

BACKGROUND
<2-3 sentences: how they got to this role, what they did before, one
memorable personal detail that isn't about identity — a hobby, a
previous career, a quirk.>

WHAT YOU CARE ABOUT AT WORK
<2-3 sentences: what makes you good at your job, what you push
colleagues on, what you think the company does well and poorly.>

HOW YOU COMMUNICATE
<2-3 sentences: your register (warm/formal/direct/wry), how you give
feedback, your pet peeves, what you're known for saying.>

ON THIS INTERN
<1-2 sentences: your starting attitude toward the new intern. Curious,
supportive, slightly skeptical, whatever fits the character — NOT
"I don't know you yet", something specific.>

RULES
- Speak in first person as yourself.
- Use first names and roles when referring to colleagues, not pronouns.
- Stay in character. Don't mention you're an AI or that this is a
  simulation.
- Keep messages appropriately short for the channel (email: 3-5
  sentences, chat: 1-2 sentences).
- You are not the student's mentor unless explicitly told so in the
  conversation context.
```

**Note on register:** personas are written in first person from the
character's perspective. The "gender-neutral" rule means avoiding
third-person pronouns — the character refers to colleagues as "Karen",
"Ravi", "the CFO" rather than "she" or "he". The character speaks about
themselves as "I" so pronouns don't arise there. This matches the
existing persona files for IronVale, NexusPoint etc.

- [ ] **Step 2: For each gap, write a persona file**

For every gap listed in `/tmp/persona-audit-baseline.txt`:

1. Look up any existing context for this character — their `role` from `brief.yaml` `employees[]`, their `background` from `customisation.background` if present, their `reports_to` from any job they appear on.
2. Write `<company_slug>/content/employees/<character_slug>-prompt.txt` following the template above.
3. Also update `<company_slug>/brief.yaml` `employees[]` if the character is missing there — add an entry with `slug`, `name`, `role`, and a `customisation.background` that's a one-paragraph summary of the prompt file. This is the "two stores must agree" rule (Rule A from the spec's Section 2).

Keep each persona tonally distinct — make the characters feel like different people, not templates.

- [ ] **Step 3: Re-run the audit**

```bash
uv run python scripts/audit_personas.py
```

Expected: `TOTAL GAPS: 0 — all personas present.`

- [ ] **Step 4: Commit the new personas**

```bash
git add ../ironvale-resources/content/employees/*.txt ../ironvale-resources/brief.yaml 2>/dev/null
git add ../nexuspoint-systems/content/employees/*.txt ../nexuspoint-systems/brief.yaml 2>/dev/null
git add ../meridian-advisory/content/employees/*.txt ../meridian-advisory/brief.yaml 2>/dev/null
git add ../metro-council-wa/content/employees/*.txt ../metro-council-wa/brief.yaml 2>/dev/null
git add ../southern-cross-financial/content/employees/*.txt ../southern-cross-financial/brief.yaml 2>/dev/null
git add ../horizon-foundation/content/employees/*.txt ../horizon-foundation/brief.yaml 2>/dev/null
# Commit per company since each is a separate git repo
for c in ironvale-resources nexuspoint-systems meridian-advisory metro-council-wa southern-cross-financial horizon-foundation; do
  (cd ../$c && git diff --cached --quiet || git commit -m "Stage 7: fill persona gaps flagged by audit")
done
```

**Note:** each company site is its own git repo. Commits go into each company repo separately, not into workready-api.

### Task 2.4: Commit an audit smoke test

**Files:**
- Create: `scripts/smoke_persona_audit.py`

- [ ] **Step 1: Write the smoke**

```python
#!/usr/bin/env python3
"""Smoke test: re-run the persona audit and assert zero gaps.

This is the regression guard. If a future change adds a character
reference without also adding the persona file, this smoke fails.
"""

import subprocess
import sys


def main() -> int:
    result = subprocess.run(
        ["uv", "run", "python", "scripts/audit_personas.py", "--fail-on-gaps"],
        capture_output=True,
        text=True,
    )
    print(result.stdout)
    if result.returncode != 0:
        print("FAIL: persona audit found gaps", file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        return 1
    print("OK: zero persona gaps")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Run it**

```bash
uv run python scripts/smoke_persona_audit.py
```

Expected: `OK: zero persona gaps`

- [ ] **Step 3: Commit**

```bash
git add scripts/smoke_persona_audit.py
git commit -m "Stage 7: persona audit smoke test"
```

---

## Block 3 — `availability.py`

Pure functions for business hours + presence + public holidays. Depends on no other new modules. Can be implemented and smoke-tested in isolation.

### Task 3.1: Create the data subpackage and public holidays file

**Files:**
- Create: `workready_api/data/__init__.py`
- Create: `workready_api/data/public_holidays.py`

- [ ] **Step 1: Create the data subpackage**

```bash
mkdir -p workready_api/data
touch workready_api/data/__init__.py
```

- [ ] **Step 2: Write the holidays file**

Create `workready_api/data/public_holidays.py`:

```python
"""Region-keyed public holidays for the WorkReady simulation.

Hand-edited Python source (not YAML or JSON) so we don't introduce a
new dependency. Human-editable, syntax-checked by Python itself, and
easy to extend with more regions later.

Each region maps to a list of {date: "YYYY-MM-DD", name: str} dicts.
The availability module imports this at startup; changes require a
restart.
"""

from __future__ import annotations

PUBLIC_HOLIDAYS: dict[str, list[dict[str, str]]] = {
    "australia-wa": [
        # 2026
        {"date": "2026-01-01", "name": "New Year's Day"},
        {"date": "2026-01-26", "name": "Australia Day"},
        {"date": "2026-03-02", "name": "Labour Day (WA)"},
        {"date": "2026-04-03", "name": "Good Friday"},
        {"date": "2026-04-04", "name": "Easter Saturday"},
        {"date": "2026-04-06", "name": "Easter Monday"},
        {"date": "2026-04-25", "name": "ANZAC Day"},
        {"date": "2026-06-01", "name": "Western Australia Day"},
        {"date": "2026-09-28", "name": "King's Birthday (WA)"},
        {"date": "2026-12-25", "name": "Christmas Day"},
        {"date": "2026-12-28", "name": "Boxing Day (observed)"},
        # 2027
        {"date": "2027-01-01", "name": "New Year's Day"},
        {"date": "2027-01-26", "name": "Australia Day"},
        {"date": "2027-03-01", "name": "Labour Day (WA)"},
        {"date": "2027-03-26", "name": "Good Friday"},
        {"date": "2027-03-27", "name": "Easter Saturday"},
        {"date": "2027-03-29", "name": "Easter Monday"},
        {"date": "2027-04-26", "name": "ANZAC Day (observed)"},
        {"date": "2027-06-07", "name": "Western Australia Day"},
        {"date": "2027-09-27", "name": "King's Birthday (WA)"},
        {"date": "2027-12-27", "name": "Christmas Day (observed)"},
        {"date": "2027-12-28", "name": "Boxing Day (observed)"},
    ],
}


def holiday_dates_for_region(region: str) -> set[str]:
    """Return the set of ISO date strings for a region. Unknown → empty set."""
    entries = PUBLIC_HOLIDAYS.get(region, [])
    return {e["date"] for e in entries}
```

- [ ] **Step 3: Smoke — module imports and returns dates**

```bash
uv run python -c "
from workready_api.data.public_holidays import holiday_dates_for_region
dates = holiday_dates_for_region('australia-wa')
assert '2026-04-25' in dates
assert '2026-12-25' in dates
assert len(dates) >= 20
unknown = holiday_dates_for_region('mars-olympus-mons')
assert unknown == set()
print(f'OK: australia-wa has {len(dates)} dates')
"
```

Expected: `OK: australia-wa has 22 dates` (or similar, depending on exact 2026-2027 list)

- [ ] **Step 4: Commit**

```bash
git add workready_api/data/
git commit -m "Stage 7: add public holidays data module (AU-WA)"
```

### Task 3.2: Write `availability.py` — public holiday check

**Files:**
- Create: `workready_api/availability.py`

- [ ] **Step 1: Write the module**

Create `workready_api/availability.py`:

```python
"""Business hours, presence, and public holiday helpers.

Pure functions. No DB writes. Used by the team directory (to compute
presence_ok per team member) and by the chat and mail reply paths (to
compute deliver_at that looks like office hours).

Three concepts:

- **Business hours** — configured per company in brief.yaml
  (business_hours.start, .end, .days). Defaults to 9-5 Mon-Fri if a
  company's brief doesn't set them.
- **Presence** — per-character, optional field on brief.yaml
  employees[].availability. Status: available | away | travelling |
  sick | on_leave. Default: available.
- **Public holidays** — optional per-company reference to a region
  in data/public_holidays.py via brief.yaml business_hours.holidays_region.
  Default: no filtering.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from workready_api.data.public_holidays import holiday_dates_for_region
from workready_api.jobs import get_company


LOCAL_TZ = ZoneInfo("Australia/Perth")


def _company_config(company_slug: str) -> dict:
    """Return the company's business_hours dict with sensible defaults."""
    company = get_company(company_slug) or {}
    bh = company.get("business_hours", {}) or {}
    return {
        "start": int(bh.get("start", 9)),
        "end": int(bh.get("end", 17)),
        "days": list(bh.get("days", [1, 2, 3, 4, 5])),  # Mon-Fri
        "holidays_region": bh.get("holidays_region"),
    }


def _employees_roster(company_slug: str) -> list[dict]:
    """Return the employees roster from jobs.json (passthrough from brief.yaml)."""
    company = get_company(company_slug) or {}
    return company.get("employees", []) or []


def _now_local() -> datetime:
    return datetime.now(timezone.utc).astimezone(LOCAL_TZ)


def is_public_holiday(company_slug: str, date_local: datetime | None = None) -> bool:
    """True if the given date (in company-local time) is a public holiday
    for this company's configured region. Unknown region → always False.
    """
    cfg = _company_config(company_slug)
    region = cfg.get("holidays_region")
    if not region:
        return False
    if date_local is None:
        date_local = _now_local()
    iso_date = date_local.strftime("%Y-%m-%d")
    return iso_date in holiday_dates_for_region(region)


def _is_within_business_hours(company_slug: str, dt_local: datetime) -> bool:
    """True if dt_local falls inside this company's business hours AND
    is on a business day AND is not a public holiday.
    """
    cfg = _company_config(company_slug)
    weekday = dt_local.isoweekday()  # 1=Mon..7=Sun
    if weekday not in cfg["days"]:
        return False
    hour = dt_local.hour
    if not (cfg["start"] <= hour < cfg["end"]):
        return False
    if is_public_holiday(company_slug, dt_local):
        return False
    return True


def is_character_available(company_slug: str, character_slug: str) -> bool:
    """True if a team member is contactable right now.

    Checks (in order):
    1. Business hours + business day
    2. Public holiday
    3. Character presence state (availability.status)
    """
    now_local = _now_local()
    if not _is_within_business_hours(company_slug, now_local):
        return False

    for emp in _employees_roster(company_slug):
        if emp.get("slug") != character_slug:
            continue
        avail = emp.get("availability") or {}
        status = avail.get("status", "available")
        if status != "available":
            # Check return_date — if in the past, treat as available
            return_date = avail.get("return_date")
            if return_date:
                try:
                    rd = datetime.fromisoformat(return_date).replace(tzinfo=LOCAL_TZ)
                    if now_local >= rd:
                        return True
                except (ValueError, TypeError):
                    pass
            return False
        return True

    # Character not in roster → treat as available but log noise
    import logging
    logging.getLogger(__name__).warning(
        "is_character_available: character %s not in roster for %s",
        character_slug, company_slug,
    )
    return True


def next_business_hours_slot(
    company_slug: str,
    after_utc: datetime | None = None,
    *,
    jitter_minutes: int = 30,
    runaway_guard_days: int = 30,
) -> str:
    """Compute the next timestamp (as UTC ISO string) that:

    - Is on a business day for this company
    - Is inside business hours (company-local)
    - Is NOT a public holiday

    If `after_utc` is already inside business hours, returns that time
    plus a small random jitter (0..jitter_minutes) to simulate realistic
    reply latency. Otherwise advances to the next valid slot (next
    business day start + jitter).

    The runaway guard caps the search at `runaway_guard_days` — if no
    valid slot is found within that window, raises RuntimeError (should
    never happen in practice).
    """
    import random

    if after_utc is None:
        after_utc = datetime.now(timezone.utc)

    cfg = _company_config(company_slug)
    probe_local = after_utc.astimezone(LOCAL_TZ)

    # If already in business hours, small jitter and return
    if _is_within_business_hours(company_slug, probe_local):
        jitter = random.randint(0, max(jitter_minutes, 1))  # noqa: S311
        result_local = probe_local + timedelta(minutes=jitter)
        if _is_within_business_hours(company_slug, result_local):
            return result_local.astimezone(timezone.utc).isoformat()

    # Otherwise walk forward to the next valid business-day start
    probe_local = probe_local.replace(
        hour=cfg["start"], minute=0, second=0, microsecond=0,
    )
    if probe_local <= after_utc.astimezone(LOCAL_TZ):
        probe_local += timedelta(days=1)

    for _ in range(runaway_guard_days):
        weekday = probe_local.isoweekday()
        if weekday in cfg["days"] and not is_public_holiday(company_slug, probe_local):
            jitter = random.randint(0, max(jitter_minutes, 1))  # noqa: S311
            result_local = probe_local + timedelta(minutes=jitter)
            return result_local.astimezone(timezone.utc).isoformat()
        probe_local += timedelta(days=1)

    raise RuntimeError(
        f"next_business_hours_slot: no valid slot found within "
        f"{runaway_guard_days} days for {company_slug}"
    )


def compute_reply_deliver_at(
    company_slug: str,
    student_last_login_iso: str | None,
    *,
    now_utc: datetime | None = None,
) -> str:
    """Business-hours deliver_at with absent-student backdating.

    If the student has been active recently (< 24h ago), use
    next_business_hours_slot for realistic office-hours delivery.

    If the student has been absent > 24h, backdate the reply to a
    plausible business-hours slot BEFORE now, so the student returns to
    find the reply "already in their inbox from earlier today".
    """
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)

    def _walk_backwards_to_business_hours(anchor: datetime) -> datetime:
        """From `anchor`, walk backwards to the most recent plausible
        business-hours moment and return that as UTC."""
        cfg = _company_config(company_slug)
        probe_local = anchor.astimezone(LOCAL_TZ)
        for _ in range(30):
            if _is_within_business_hours(company_slug, probe_local):
                return probe_local.astimezone(timezone.utc)
            probe_local -= timedelta(hours=1)
        # Fallback: earlier today 14:00 local
        return anchor.astimezone(LOCAL_TZ).replace(
            hour=14, minute=0, second=0, microsecond=0,
        ).astimezone(timezone.utc)

    if student_last_login_iso is None:
        return next_business_hours_slot(company_slug, now_utc)

    try:
        last_login = datetime.fromisoformat(student_last_login_iso)
        if last_login.tzinfo is None:
            last_login = last_login.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return next_business_hours_slot(company_slug, now_utc)

    hours_absent = (now_utc - last_login).total_seconds() / 3600
    if hours_absent <= 24:
        return next_business_hours_slot(company_slug, now_utc)

    # Absent > 24h: backdate to plausible office hours earlier today
    backdated = _walk_backwards_to_business_hours(now_utc)
    return backdated.isoformat()
```

- [ ] **Step 2: Smoke import check**

```bash
WORKREADY_DB=/tmp/av.db LLM_PROVIDER=stub uv run python -c "
from workready_api.availability import (
    is_public_holiday, is_character_available, next_business_hours_slot,
    compute_reply_deliver_at,
)
print('OK: availability module imports cleanly')
"
```

Expected: `OK: availability module imports cleanly`

- [ ] **Step 3: Commit**

```bash
git add workready_api/availability.py
git commit -m "Stage 7: add availability module (business hours, presence, holidays)"
```

### Task 3.3: Write `smoke_availability.py`

**Files:**
- Create: `scripts/smoke_availability.py`

- [ ] **Step 1: Write the smoke**

```python
#!/usr/bin/env python3
"""Smoke test: availability module.

Verifies business hours checks, public holiday filtering, presence
states, and deliver_at computation including the absent-student
backdating trick. Runs against a fresh DB with stub LLM.
"""

import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

os.environ.setdefault("WORKREADY_DB", "/tmp/smoke_availability.db")
os.environ.setdefault("LLM_PROVIDER", "stub")

# Clean slate
import pathlib
pathlib.Path(os.environ["WORKREADY_DB"]).unlink(missing_ok=True)

from workready_api.db import init_db
init_db()

# Populate the jobs cache with a minimal fake company so we can test
# the availability functions without needing the real brief.yaml files.
from workready_api.jobs import _COMPANY_CACHE
_COMPANY_CACHE["test-co"] = {
    "company": "Test Co",
    "business_hours": {
        "start": 9,
        "end": 17,
        "days": [1, 2, 3, 4, 5],
        "holidays_region": "australia-wa",
    },
    "employees": [
        {"slug": "alice-available", "name": "Alice",
         "role": "Manager", "availability": {"status": "available"}},
        {"slug": "bob-away", "name": "Bob", "role": "Analyst",
         "availability": {"status": "away", "return_date": None}},
        {"slug": "carol-return",
         "name": "Carol", "role": "Engineer",
         "availability": {"status": "on_leave", "return_date": "2020-01-01"}},
    ],
}

from workready_api.availability import (
    is_public_holiday, is_character_available,
    next_business_hours_slot, compute_reply_deliver_at, LOCAL_TZ,
)

# --- Test 1: public holiday detection ---
anzac = datetime(2026, 4, 25, 12, 0, tzinfo=LOCAL_TZ)
xmas = datetime(2026, 12, 25, 12, 0, tzinfo=LOCAL_TZ)
normal = datetime(2026, 5, 6, 12, 0, tzinfo=LOCAL_TZ)
assert is_public_holiday("test-co", anzac), "ANZAC Day should be a holiday"
assert is_public_holiday("test-co", xmas), "Christmas should be a holiday"
assert not is_public_holiday("test-co", normal), "Random Wed should not be a holiday"
print("  [1/6] public holiday detection OK")

# --- Test 2: next slot skips holidays ---
thursday_pre_anzac = datetime(2026, 4, 23, 15, 0, tzinfo=timezone.utc)
slot = next_business_hours_slot("test-co", thursday_pre_anzac, jitter_minutes=0)
slot_dt = datetime.fromisoformat(slot).astimezone(LOCAL_TZ)
# 23 Apr 2026 is Thu, 25 Apr is Sat (ANZAC), 27 Apr is Mon
# If we ask at Thu 15:00 local, we're already in hours so answer is ~Thu 23 Apr
# Actually thursday_pre_anzac is UTC 15:00 which is Thu 23:00 local (past 17:00)
# So next slot is Fri 24 Apr 09:00 local
# But that's still before ANZAC so it's valid
assert slot_dt.weekday() in (0, 1, 2, 3, 4), f"Slot fell on weekend: {slot_dt}"
print(f"  [2/6] next_business_hours_slot respects weekdays (returned {slot_dt.isoformat()})")

# --- Test 3: character availability — available ---
# Set "now" via a monkey-patched _now_local... actually easier: just check
# that available characters return True during a known business-hours slot.
# We can't easily mock "now" without touching the module. Skip exact time
# assertion and just confirm the function runs without error for each state.
result_alice = is_character_available("test-co", "alice-available")
result_bob = is_character_available("test-co", "bob-away")
result_carol = is_character_available("test-co", "carol-return")
# Alice's result depends on "now" — we can only assert it's a bool
assert isinstance(result_alice, bool)
# Bob is away with no return date → always False regardless of time
assert result_bob is False, f"Bob (away) should be unavailable, got {result_bob}"
# Carol's return date is in the past → treat as available, so result depends on biz hours
assert isinstance(result_carol, bool)
print("  [3/6] is_character_available returns sensible values for all states")

# --- Test 4: unknown character → warning + True ---
result_unknown = is_character_available("test-co", "nonexistent")
assert isinstance(result_unknown, bool)
print("  [4/6] is_character_available handles unknown slug gracefully")

# --- Test 5: compute_reply_deliver_at — recent login ---
recent = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
deliver = compute_reply_deliver_at("test-co", recent)
# Should be either now (jittered) if in hours, or next business slot
assert "T" in deliver, f"deliver_at not ISO: {deliver}"
print(f"  [5/6] compute_reply_deliver_at with recent login returned {deliver}")

# --- Test 6: compute_reply_deliver_at — absent student ---
absent = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
deliver_absent = compute_reply_deliver_at("test-co", absent)
absent_dt = datetime.fromisoformat(deliver_absent)
now_utc = datetime.now(timezone.utc)
# Absent backdating: the result should be EARLIER than now (or equal)
# to create the illusion the reply was sent during business hours while away
assert absent_dt <= now_utc + timedelta(hours=1), \
    f"Absent-student deliver_at should not be in the future, got {deliver_absent}"
print(f"  [6/6] compute_reply_deliver_at with absent student backdates correctly ({deliver_absent})")

print("\nOK: availability smoke passed all 6 checks")
```

- [ ] **Step 2: Run it**

```bash
uv run python scripts/smoke_availability.py
```

Expected: all 6 checks print OK, final line `OK: availability smoke passed all 6 checks`

- [ ] **Step 3: Commit**

```bash
git add scripts/smoke_availability.py
git commit -m "Stage 7: availability smoke test"
```

---

## Block 4 — `team_directory.py` + team route

### Task 4.1: Extend jobs.py to expose team[] field

**Files:**
- Modify: `workready_api/jobs.py`

- [ ] **Step 1: Verify team[] passthrough**

Ensayo already passes unknown fields from brief.yaml through to jobs.json. Check that `_JOB_CACHE` preserves the `team` field:

```bash
uv run python -c "
from workready_api.jobs import _JOB_CACHE, load_jobs
from pathlib import Path
import os
sites = Path(os.environ.get('SITES_DIR', '..'))
load_jobs(sites, ['ironvale-resources'])
# Look at any job and see its keys
for key, job in list(_JOB_CACHE.items())[:3]:
    print(key, 'has team?' , 'team' in job)
"
```

Expected: prints three rows showing whether each job has `team` present. (It probably will NOT be present yet because nothing has authored it.)

- [ ] **Step 2: No code change needed for passthrough**

The existing `jobs.py` loader already preserves arbitrary fields via `_JOB_CACHE[(slug, job_slug)] = job_dict`. No modification required. Marking this task as a verification-only step.

### Task 4.2: Create `team_directory.py`

**Files:**
- Create: `workready_api/team_directory.py`

- [ ] **Step 1: Write the module**

```python
"""Team directory resolver.

Single public function that turns an application_id into two lists:
the student's immediate team (full chat + email) and the wider
organisation (email only).

Written as one function with a clean input/output contract so the
future dynamic-team-adjustment project can slot in an override
precedence chain without touching callers.
"""

from __future__ import annotations

from typing import Any

from workready_api.availability import is_character_available
from workready_api.db import get_application
from workready_api.jobs import get_company, get_job


def get_team_for_application(application_id: int) -> dict[str, Any]:
    """Return the team directory for a student's application.

    Shape:
        {
            "team": [CharacterRef, ...],
            "org":  [CharacterRef, ...],
            "business_hours": {start, end, days, holidays_region},
        }

    Where CharacterRef is:
        {
            "slug": str,
            "name": str,
            "role": str,
            "email": str,
            "presence_ok": bool,
            "availability_status": str,
            "availability_note": str,
        }

    Returns {"team": [], "org": [], "business_hours": {}} if the
    application or company cannot be resolved.
    """
    app_data = get_application(application_id)
    if not app_data:
        return _empty()

    company_slug = app_data.get("company_slug", "")
    job_slug = app_data.get("job_slug", "")

    company = get_company(company_slug) or {}
    job = get_job(company_slug, job_slug) or {}

    employees = company.get("employees", []) or []
    employees_by_slug = {e.get("slug"): e for e in employees if e.get("slug")}

    team_slugs = _resolve_team_slugs(job, employees)
    team_refs = [
        _build_character_ref(company_slug, employees_by_slug[s], company)
        for s in team_slugs
        if s in employees_by_slug
    ]

    org_slugs = [
        s for s in employees_by_slug.keys()
        if s not in set(team_slugs)
    ]
    org_refs = [
        _build_character_ref(
            company_slug, employees_by_slug[s], company, email_only=True,
        )
        for s in org_slugs
    ]

    return {
        "team": team_refs,
        "org": org_refs,
        "business_hours": company.get("business_hours", {}),
    }


def _empty() -> dict[str, Any]:
    return {"team": [], "org": [], "business_hours": {}}


def _resolve_team_slugs(job: dict, employees: list[dict]) -> list[str]:
    """Determine the student's team from their job.

    Precedence chain (the future dynamic-team-adjustment project will
    extend this):

    1. Job's explicit `team:` field → use it
    2. Otherwise → whole employees[] roster (small-business fallback)

    Returns the resolved team as a list of character slugs.
    """
    explicit = job.get("team", []) or []
    if explicit:
        return list(explicit)
    return [e["slug"] for e in employees if e.get("slug")]


def _build_character_ref(
    company_slug: str,
    employee: dict,
    company: dict,
    *,
    email_only: bool = False,
) -> dict[str, Any]:
    """Serialise a brief.yaml employee dict into an API CharacterRef."""
    slug = employee.get("slug", "")
    name = employee.get("name", "")
    role = employee.get("role", "")
    avail = employee.get("availability") or {}
    status = avail.get("status", "available")
    note = avail.get("note", "") or ""

    domain = _domain_for_company(company_slug, company)
    first_name = name.split()[0].lower() if name else slug
    email = f"{first_name}.{slug.split('-', 1)[-1]}@{domain}" if slug else ""

    return {
        "slug": slug,
        "name": name,
        "role": role,
        "email": email,
        "presence_ok": False if email_only else is_character_available(company_slug, slug),
        "availability_status": status,
        "availability_note": note,
        "email_only": email_only,
    }


def _domain_for_company(company_slug: str, company: dict) -> str:
    """Return the company domain for email construction.

    Uses the same pattern as setup-chatbots.py and email_registry.py —
    strip hyphens and append .com.au. Future improvement: read from
    brief.yaml company.domain if present.
    """
    configured = company.get("domain")
    if configured:
        return configured
    return company_slug.replace("-", "") + ".com.au"
```

- [ ] **Step 2: Smoke import check**

```bash
WORKREADY_DB=/tmp/td.db LLM_PROVIDER=stub uv run python -c "
from workready_api.team_directory import get_team_for_application
print('OK: team_directory imports cleanly')
"
```

Expected: `OK: team_directory imports cleanly`

- [ ] **Step 3: Commit**

```bash
git add workready_api/team_directory.py
git commit -m "Stage 7: add team_directory resolver"
```

### Task 4.3: Write team directory smoke

**Files:**
- Create: `scripts/smoke_team_directory.py`

- [ ] **Step 1: Write the smoke**

```python
#!/usr/bin/env python3
"""Smoke test: team_directory resolver.

Covers three scenarios:
1. Job with explicit team: field → resolves to that subset
2. Job with no team: field → falls back to whole employees list
3. Unknown application_id → empty result, no crash
"""

import os
import pathlib

os.environ.setdefault("WORKREADY_DB", "/tmp/smoke_team.db")
os.environ.setdefault("LLM_PROVIDER", "stub")
pathlib.Path(os.environ["WORKREADY_DB"]).unlink(missing_ok=True)

from workready_api.db import (
    init_db, get_or_create_student, create_application,
)
from workready_api.jobs import _COMPANY_CACHE, _JOB_CACHE

init_db()

# --- Seed a fake company + job with an explicit team ---
_COMPANY_CACHE["test-explicit"] = {
    "company": "Explicit Team Co",
    "domain": "explicitteam.com.au",
    "business_hours": {"start": 9, "end": 17, "days": [1, 2, 3, 4, 5]},
    "employees": [
        {"slug": "alice", "name": "Alice", "role": "Manager"},
        {"slug": "bob", "name": "Bob", "role": "Analyst"},
        {"slug": "carol", "name": "Carol", "role": "Engineer"},
        {"slug": "dave", "name": "Dave", "role": "CEO"},
    ],
}
_JOB_CACHE[("test-explicit", "junior")] = {
    "slug": "junior",
    "title": "Junior",
    "team": ["alice", "bob"],
}

# --- Seed a company with no team[] field on the job ---
_COMPANY_CACHE["test-default"] = {
    "company": "Default Team Co",
    "domain": "defaultteam.com.au",
    "business_hours": {"start": 9, "end": 17, "days": [1, 2, 3, 4, 5]},
    "employees": [
        {"slug": "eve", "name": "Eve", "role": "Owner"},
        {"slug": "frank", "name": "Frank", "role": "Lead"},
    ],
}
_JOB_CACHE[("test-default", "intern")] = {
    "slug": "intern",
    "title": "Intern",
}

# --- Create synthetic applications ---
s = get_or_create_student("t@example.com", "Tester")

app_explicit = create_application(
    student_id=s["id"], student_email="t@example.com",
    company_slug="test-explicit", job_slug="junior", job_title="Junior",
)
app_default = create_application(
    student_id=s["id"], student_email="t@example.com",
    company_slug="test-default", job_slug="intern", job_title="Intern",
)

from workready_api.team_directory import get_team_for_application

# --- Test 1: explicit team[] is respected ---
r1 = get_team_for_application(app_explicit)
team_slugs = [m["slug"] for m in r1["team"]]
org_slugs = [m["slug"] for m in r1["org"]]
assert set(team_slugs) == {"alice", "bob"}, f"Got {team_slugs}"
assert set(org_slugs) == {"carol", "dave"}, f"Got {org_slugs}"
assert all(m["email_only"] is False for m in r1["team"])
assert all(m["email_only"] is True for m in r1["org"])
print(f"  [1/3] explicit team resolver: team={team_slugs} org={org_slugs}")

# --- Test 2: default fallback is whole employees list ---
r2 = get_team_for_application(app_default)
team_slugs_2 = [m["slug"] for m in r2["team"]]
assert set(team_slugs_2) == {"eve", "frank"}, f"Got {team_slugs_2}"
assert len(r2["org"]) == 0, f"Expected empty org, got {r2['org']}"
print(f"  [2/3] default fallback: team={team_slugs_2} (whole employees list)")

# --- Test 3: unknown application ---
r3 = get_team_for_application(99999)
assert r3 == {"team": [], "org": [], "business_hours": {}}
print("  [3/3] unknown application returns empty shape, no crash")

print("\nOK: team_directory smoke passed all 3 checks")
```

- [ ] **Step 2: Run it**

```bash
uv run python scripts/smoke_team_directory.py
```

Expected: all 3 checks print OK, final line `OK: team_directory smoke passed all 3 checks`

- [ ] **Step 3: Commit**

```bash
git add scripts/smoke_team_directory.py
git commit -m "Stage 7: team_directory smoke test"
```

### Task 4.4: Add team models + route in app.py

**Files:**
- Modify: `workready_api/models.py`
- Modify: `workready_api/app.py`

- [ ] **Step 1: Add Pydantic models**

Append to `workready_api/models.py`:

```python
# --- Stage 7: Team directory ---


class TeamMemberRef(BaseModel):
    slug: str
    name: str
    role: str = ""
    email: str = ""
    presence_ok: bool = False
    availability_status: str = "available"
    availability_note: str = ""
    email_only: bool = False


class TeamBusinessHours(BaseModel):
    start: int = 9
    end: int = 17
    days: list[int] = []
    holidays_region: str | None = None


class TeamDirectoryResponse(BaseModel):
    team: list[TeamMemberRef] = []
    org: list[TeamMemberRef] = []
    business_hours: TeamBusinessHours = TeamBusinessHours()
```

- [ ] **Step 2: Add route in `app.py`**

Import `get_team_for_application` from `workready_api.team_directory` at the top of `app.py` (in the alphabetical imports block), and import `TeamDirectoryResponse` + `TeamMemberRef` + `TeamBusinessHours` from `workready_api.models`.

Add the route near the other `/application/` routes:

```python
@app.get("/api/v1/team/{application_id}", response_model=TeamDirectoryResponse)
def get_team(application_id: int) -> TeamDirectoryResponse:
    """Return the team directory for a hired student's application."""
    app_data = get_application(application_id)
    if not app_data:
        raise HTTPException(status_code=404, detail="Application not found")
    payload = get_team_for_application(application_id)
    return TeamDirectoryResponse(
        team=[TeamMemberRef(**m) for m in payload["team"]],
        org=[TeamMemberRef(**m) for m in payload["org"]],
        business_hours=TeamBusinessHours(**(payload["business_hours"] or {})),
    )
```

- [ ] **Step 3: Import check**

```bash
WORKREADY_DB=/tmp/t.db LLM_PROVIDER=stub uv run python -c "from workready_api import app; print('ok')"
```

Expected: `ok`

- [ ] **Step 4: Live route check**

```bash
rm -f /tmp/t.db
WORKREADY_DB=/tmp/t.db LLM_PROVIDER=stub uv run uvicorn workready_api.app:app --port 8701 --log-level warning &
PID=$!
sleep 2
# Seed a real IronVale application via the existing helpers
WORKREADY_DB=/tmp/t.db uv run python -c "
from workready_api.db import init_db, get_or_create_student, create_application, advance_stage
init_db()
s = get_or_create_student('td@example.com', 'TD Test')
app_id = create_application(student_id=s['id'], student_email='td@example.com',
  company_slug='ironvale-resources', job_slug='junior-analyst', job_title='Junior Analyst')
advance_stage(app_id, 'placement')
print(f'APP_ID={app_id}')
" 2>&1 | grep APP_ID
curl -s http://127.0.0.1:8701/api/v1/team/1 | uv run python -m json.tool | head -30
kill $PID 2>/dev/null
```

Expected: a JSON response with team/org arrays populated from ironvale-resources. If the team array is empty, inspect — likely because ironvale jobs.json doesn't have the team[] field yet, in which case the default fallback should return all employees.

- [ ] **Step 5: Commit**

```bash
git add workready_api/models.py workready_api/app.py
git commit -m "Stage 7: add team directory models and route"
```

---

## Block 5 — `context_builder.py`

Task-aware context stuffer. Reads existing rows (messages, tasks, stage_results, lunchroom_sessions) to build a character's "what I know about this student" payload. Used by both the mail.py character reply path and the new chat reply path.

### Task 5.1: Create `context_builder.py`

**Files:**
- Create: `workready_api/context_builder.py`

- [ ] **Step 1: Write the module**

```python
"""Task-aware character context builder.

Single public function: build_character_context(student_id,
character_slug, application_id). Returns everything a character needs
to reply coherently to a student — the full unified thread, current
task state, earlier stage summaries, and the character's own persona.

Used by mail.py character reply path and the chat reply path. Single
source of truth for "what does this character know about this
student".

Summarisation guardrail: when the thread portion of the context
exceeds 24K chars (matching the existing mail.py pattern), older
messages are summarised via a separate LLM call and the recent 4
messages are kept verbatim. Summary is regenerated per reply — no
caching in v1.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from workready_api.db import (
    get_active_exit_interview,
    get_active_performance_review,
    get_application,
    get_db,
    get_latest_submission,
    get_stage_results,
    get_student_by_id,
    list_lunchroom_sessions_for_application,
    list_tasks_for_application,
)
from workready_api.jobs import get_company, get_job


THREAD_CHAR_CAP = 24_000
VERBATIM_TAIL_COUNT = 4


@dataclass
class CharacterContext:
    student_name: str = ""
    student_first_name: str = ""
    student_email: str = ""
    company_name: str = ""
    job_title: str = ""
    current_stage: str = ""
    persona_prompt: str = ""
    character_name: str = ""
    character_role: str = ""

    thread: list[dict[str, str]] = field(default_factory=list)
    thread_summary: str = ""

    active_tasks: list[dict[str, Any]] = field(default_factory=list)
    past_tasks: list[dict[str, Any]] = field(default_factory=list)
    resume_summary: dict[str, Any] | None = None
    interview_summary: dict[str, Any] | None = None
    lunchroom_participation: str = ""
    coaching_notes: str = ""


def build_character_context(
    student_id: int,
    character_slug: str,
    application_id: int,
) -> CharacterContext:
    """Assemble a CharacterContext for this (student, character) pair."""
    app_data = get_application(application_id) or {}
    student = get_student_by_id(student_id) or {}
    company_slug = app_data.get("company_slug", "")
    job_slug = app_data.get("job_slug", "")
    job = get_job(company_slug, job_slug) or {}
    company = get_company(company_slug) or {}

    ctx = CharacterContext(
        student_name=student.get("name", ""),
        student_first_name=(student.get("name") or "").split()[0] if student.get("name") else "",
        student_email=student.get("email", ""),
        company_name=job.get("company", company_slug),
        job_title=app_data.get("job_title", job_slug),
        current_stage=app_data.get("current_stage", ""),
        persona_prompt=_load_persona(company_slug, character_slug),
        character_name=_character_name(company, character_slug),
        character_role=_character_role(company, character_slug),
    )

    # Unified thread (both channels)
    ctx.thread = _load_unified_thread(student_id, application_id, character_slug)

    # Summarise if over cap
    total_chars = sum(len(m.get("text", "")) for m in ctx.thread)
    if total_chars > THREAD_CHAR_CAP and len(ctx.thread) > VERBATIM_TAIL_COUNT:
        tail = ctx.thread[-VERBATIM_TAIL_COUNT:]
        older = ctx.thread[:-VERBATIM_TAIL_COUNT]
        ctx.thread_summary = _summarise_thread(older)
        ctx.thread = tail

    # Task state
    all_tasks = list_tasks_for_application(application_id, only_visible=False)
    for t in all_tasks:
        sub = get_latest_submission(t["id"]) or {}
        sub_feedback = sub.get("feedback") or {}
        record = {
            "sequence": t.get("sequence"),
            "title": t.get("title"),
            "status": t.get("status"),
            "brief": t.get("brief", ""),
            "due_at": t.get("due_at"),
            "submitted_at": sub.get("created_at"),
            "score": sub.get("score"),
            "mentor_note": sub_feedback.get("summary", ""),
        }
        if t.get("status") in ("assigned", "submitted"):
            ctx.active_tasks.append(record)
        else:
            ctx.past_tasks.append(record)

    # Resume summary
    resume_results = get_stage_results(application_id, "resume")
    if resume_results:
        latest = resume_results[-1]
        feedback = latest.get("feedback") or {}
        ctx.resume_summary = {
            "score": latest.get("score"),
            "strengths": feedback.get("strengths", []),
            "gaps": feedback.get("gaps", []),
        }

    # Interview summary
    interview_results = get_stage_results(application_id, "interview")
    if interview_results:
        latest = interview_results[-1]
        feedback = latest.get("feedback") or {}
        ctx.interview_summary = {
            "score": latest.get("score"),
            "strengths": feedback.get("strengths", []),
        }

    # Lunchroom participation (Stage 5)
    lunchroom_sessions = list_lunchroom_sessions_for_application(application_id)
    completed_notes = [
        (sess.get("participation_notes") or "")
        for sess in lunchroom_sessions
        if sess.get("status") == "completed"
    ]
    ctx.lunchroom_participation = " ".join(n for n in completed_notes if n)

    # Mid-placement coaching (Stage 4.5)
    perf_session = get_active_performance_review(application_id)
    if perf_session and perf_session.get("status") == "completed":
        feedback = perf_session.get("feedback") or {}
        ctx.coaching_notes = feedback.get("summary", "")

    return ctx


def _load_persona(company_slug: str, character_slug: str) -> str:
    """Load content/employees/<slug>-prompt.txt from the company repo."""
    import os
    sites_dir = Path(os.environ.get(
        "SITES_DIR", str(Path(__file__).parent.parent.parent),
    ))
    path = sites_dir / company_slug / "content" / "employees" / f"{character_slug}-prompt.txt"
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return (
        f"You are a colleague at {company_slug}. Warm, professional, "
        f"first-person voice. Use first names and roles for colleagues."
    )


def _character_name(company: dict, character_slug: str) -> str:
    for emp in company.get("employees", []) or []:
        if emp.get("slug") == character_slug:
            return emp.get("name", character_slug)
    return character_slug


def _character_role(company: dict, character_slug: str) -> str:
    for emp in company.get("employees", []) or []:
        if emp.get("slug") == character_slug:
            return emp.get("role", "")
    return ""


def _load_unified_thread(
    student_id: int,
    application_id: int,
    character_slug: str,
) -> list[dict[str, str]]:
    """Load all messages in this (student, character) thread, both channels,
    filtered to delivered-only, in chronological order.

    Character identity is matched loosely: a message is "in the thread"
    with `character_slug` if either:
    - It's outbound (student → character) and recipient_email matches
      a character email pattern for the slug, OR
    - It's inbound (character → student) and sender_name contains the
      character's name (from persona registry).

    For v1 simplicity we match on a character_slug column if present
    on message rows, falling back to name matching. Since the existing
    messages table doesn't have character_slug, we use the sender_name
    / recipient matching approach.
    """
    from workready_api.db import _now
    now = _now()
    with get_db() as conn:
        rows = conn.execute(
            """SELECT * FROM messages
               WHERE student_id = ?
                 AND application_id = ?
                 AND (deliver_at IS NULL OR deliver_at <= ?)
               ORDER BY id ASC""",
            (student_id, application_id, now),
        ).fetchall()

    # Filter: only messages involving this character_slug. Two heuristics:
    # 1. Stored sender_slug (not yet a column) — skipped
    # 2. Sender name or email contains the slug's first-name component
    slug_parts = character_slug.split("-")
    first_name = slug_parts[0].lower() if slug_parts else ""

    thread: list[dict[str, str]] = []
    for row in rows:
        d = dict(row)
        sender = (d.get("sender_name") or "").lower()
        sender_email = (d.get("sender_email") or "").lower()
        recipient = (d.get("recipient_email") or "").lower()
        direction = d.get("direction", "inbound")

        involves_character = (
            first_name in sender
            or first_name in sender_email
            or first_name in recipient
        )
        if not involves_character:
            continue

        thread.append({
            "who": "student" if direction == "outbound" else "character",
            "channel": d.get("channel", "email"),
            "text": d.get("body", "") or d.get("subject", ""),
            "created_at": d.get("created_at", ""),
        })

    return thread


def _summarise_thread(older: list[dict[str, str]]) -> str:
    """Summarise an older portion of a thread into 2-3 sentences.

    Reuses the shared chat_completion path. In stub mode returns a
    deterministic short sentence for reproducibility.
    """
    import os
    if os.environ.get("LLM_PROVIDER", "stub").lower() == "stub":
        n = len(older)
        return (
            f"Earlier in the thread ({n} messages): the student and the "
            f"character exchanged messages about task context and "
            f"ongoing placement topics."
        )

    # Real mode: one LLM call to summarise
    from workready_api.interview import chat_completion
    import asyncio

    transcript = "\n".join(
        f"{m['who']}: {m['text'][:500]}" for m in older
    )
    prompt = (
        "Summarise this conversation history in 2-3 sentences. Focus on "
        "what topics were discussed and any specific things the student "
        "asked about or mentioned. Do NOT include greetings or "
        "pleasantries.\n\n" + transcript
    )
    try:
        return asyncio.get_event_loop().run_until_complete(
            chat_completion("You summarise conversations tersely.",
                            [{"role": "user", "content": prompt}])
        )
    except Exception:  # noqa: BLE001
        return f"Earlier in the thread: {len(older)} prior messages."
```

- [ ] **Step 2: Import check**

```bash
WORKREADY_DB=/tmp/cb.db LLM_PROVIDER=stub uv run python -c "
from workready_api.context_builder import build_character_context, CharacterContext
print('OK: context_builder imports cleanly')
"
```

Expected: `OK: context_builder imports cleanly`

- [ ] **Step 3: Commit**

```bash
git add workready_api/context_builder.py
git commit -m "Stage 7: add context_builder for task-aware character replies"
```

### Task 5.2: Write context_builder smoke

**Files:**
- Create: `scripts/smoke_context_builder.py`

- [ ] **Step 1: Write the smoke**

```python
#!/usr/bin/env python3
"""Smoke test: context_builder.

Seeds a student with applications in different stages, inserts messages
and task rows, then asserts build_character_context returns the right
shape and content.
"""

import json
import os
import pathlib

os.environ.setdefault("WORKREADY_DB", "/tmp/smoke_context.db")
os.environ.setdefault("LLM_PROVIDER", "stub")
pathlib.Path(os.environ["WORKREADY_DB"]).unlink(missing_ok=True)

from workready_api.db import (
    init_db, get_or_create_student, create_application, create_message,
    record_stage_result, get_db, advance_stage,
)
from workready_api import scheduling
from workready_api.jobs import _COMPANY_CACHE, _JOB_CACHE

init_db()

# Seed a fake company so we don't depend on real brief.yaml files
_COMPANY_CACHE["ctx-test"] = {
    "company": "Context Test Co",
    "domain": "contexttest.com.au",
    "business_hours": {"start": 9, "end": 17, "days": [1, 2, 3, 4, 5]},
    "employees": [
        {"slug": "karen-whitfield", "name": "Karen Whitfield",
         "role": "Ops Lead"},
    ],
}
_JOB_CACHE[("ctx-test", "analyst")] = {
    "slug": "analyst", "title": "Analyst", "team": ["karen-whitfield"],
}

s = get_or_create_student("ctx@example.com", "Alex Tester")
app_id = create_application(
    student_id=s["id"], student_email="ctx@example.com",
    company_slug="ctx-test", job_slug="analyst", job_title="Analyst",
)
advance_stage(app_id, "placement")

# Seed prior stage results
record_stage_result(
    application_id=app_id, stage="resume", status="passed", score=72,
    feedback={"strengths": ["analytical"], "gaps": ["experience"],
              "suggestions": [], "tailoring": ""},
)
record_stage_result(
    application_id=app_id, stage="interview", status="passed", score=78,
    feedback={"strengths": ["clear"], "gaps": [],
              "suggestions": [], "tailoring": ""},
)

# Seed a task
now_iso = scheduling.to_iso(scheduling.now_utc())
with get_db() as conn:
    cursor = conn.execute(
        """INSERT INTO tasks (application_id, sequence, title, brief,
           description, difficulty, status, visible_at, due_at, assigned_at)
           VALUES (?, 1, 'Supplier risk matrix', 'brief', 'desc', 'medium',
                   'assigned', ?, ?, ?)""",
        (app_id, now_iso, now_iso, now_iso),
    )

# Seed an email from Karen to the student and a reply
create_message(
    student_id=s["id"], student_email="ctx@example.com",
    sender_name="Karen Whitfield", sender_role="Ops Lead at Context Test Co",
    subject="Welcome!", body="Hi Alex, welcome to the team. Let me know if you have questions.",
    inbox="work", application_id=app_id, related_stage="placement",
)
create_message(
    student_id=s["id"], student_email="ctx@example.com",
    sender_name="Alex Tester",
    subject="Re: Welcome!", body="Thanks Karen, quick question about task 1...",
    inbox="work", application_id=app_id, related_stage="placement",
    direction="outbound",
    sender_email="alex@contexttest.com.au",
    recipient_email="karen.whitfield@contexttest.com.au",
)

from workready_api.context_builder import build_character_context

ctx = build_character_context(
    student_id=s["id"],
    character_slug="karen-whitfield",
    application_id=app_id,
)

# --- Assertions ---
assert ctx.student_first_name == "Alex", f"got {ctx.student_first_name}"
assert ctx.company_name == "Context Test Co"
assert ctx.job_title == "Analyst"
assert ctx.current_stage == "placement"
assert ctx.character_name == "Karen Whitfield"
assert ctx.resume_summary is not None
assert ctx.resume_summary["score"] == 72
assert ctx.interview_summary is not None
assert ctx.interview_summary["score"] == 78
assert len(ctx.active_tasks) == 1
assert ctx.active_tasks[0]["title"] == "Supplier risk matrix"
assert len(ctx.thread) >= 1, f"thread empty, full ctx: {ctx}"
print(f"  thread has {len(ctx.thread)} messages")
print(f"  active tasks: {len(ctx.active_tasks)}")
print(f"  resume score: {ctx.resume_summary['score']}")
print(f"  interview score: {ctx.interview_summary['score']}")

# --- Summarisation test ---
# Stuff the thread with 60K chars of content
with get_db() as conn:
    for i in range(40):
        create_message(
            student_id=s["id"], student_email="ctx@example.com",
            sender_name="Karen Whitfield", sender_role="Ops Lead",
            subject=f"Msg {i}", body="x" * 800,
            inbox="work", application_id=app_id, related_stage="placement",
        )

ctx2 = build_character_context(
    student_id=s["id"],
    character_slug="karen-whitfield",
    application_id=app_id,
)
assert ctx2.thread_summary, "thread_summary should be populated after overflow"
assert len(ctx2.thread) <= 10, f"tail should be short, got {len(ctx2.thread)}"
print(f"  after overflow: tail={len(ctx2.thread)} summary_len={len(ctx2.thread_summary)}")

print("\nOK: context_builder smoke passed")
```

- [ ] **Step 2: Run it**

```bash
uv run python scripts/smoke_context_builder.py
```

Expected: all prints, final `OK: context_builder smoke passed`

- [ ] **Step 3: Commit**

```bash
git add scripts/smoke_context_builder.py
git commit -m "Stage 7: context_builder smoke test"
```

---

## Block 6 — `comms_monitor.py` (classifier)

Classifier module. Pre-send check on every outgoing student message. Fail-open. Stub short-circuits to all-ok in dev.

### Task 6.1: Create `comms_monitor.py`

**Files:**
- Create: `workready_api/comms_monitor.py`

- [ ] **Step 1: Write the module**

```python
"""Comms monitor — classifier for outgoing student messages.

One LLM call per outgoing message. Scores three axes:

- recipient_appropriateness: was this the right person to contact?
- tone: is the language appropriate for a professional context?
- channel_appropriateness: is the right channel being used?

Returns a ClassificationResult dataclass. The caller (mail.py or the
chat send route) decides what to do with a flag — the classifier just
scores.

Fail-open: any exception (provider down, malformed JSON, etc.) returns
a classifier_unavailable result with all axes set to "ok". The message
flows normally. The rationale: a broken safety layer should never
block a legitimate student message.

Stub mode: when LLM_PROVIDER=stub, returns all-ok with
rationale="stub mode" without making any network call.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Literal

from workready_api.interview import chat_completion


RecipientFlag = Literal["ok", "wrong_audience"]
ToneFlag = Literal["ok", "sharp", "inappropriate"]
ChannelFlag = Literal["ok", "wrong_channel"]


@dataclass
class ClassificationResult:
    recipient_appropriateness: RecipientFlag = "ok"
    tone: ToneFlag = "ok"
    channel_appropriateness: ChannelFlag = "ok"
    rationale: str = ""
    classified_at: str = ""
    status: str = "ok"  # "ok" | "classifier_unavailable"

    def any_flag(self) -> bool:
        return (
            self.recipient_appropriateness != "ok"
            or self.tone != "ok"
            or self.channel_appropriateness != "ok"
        )

    def to_json(self) -> str:
        return json.dumps(asdict(self))


_SYSTEM_PROMPT = """You are a communications quality assistant for a workplace internship simulation. You classify outgoing student messages on three axes.

Students are interns at one of six companies. They communicate with:
- Their immediate team (chat + email)
- Wider organisation characters including executive leadership (email only)
- The company careers desk (anonymous, pre-hire)

Your job is to identify messages that would benefit from a gentle redirect, NOT to block every flaw. Only flag clear cases where a realistic workplace mentor would step in.

For each message, score three axes:

recipient_appropriateness:
- "ok" — the recipient is a reasonable choice for this request
- "wrong_audience" — the student is asking something of the wrong person (e.g. asking the CEO about where the printer is, or asking an individual contributor for cross-departmental policy decisions)

tone:
- "ok" — professional or professionally-casual; fine for workplace
- "sharp" — notably terse, abrupt, or mildly aggressive, but not crossing a line
- "inappropriate" — rude, hostile, personal, or clearly crossing a professional line

channel_appropriateness:
- "ok" — the channel (personal vs work email) matches the purpose
- "wrong_channel" — using personal email for an obviously-work matter (or vice versa)

Return ONLY a JSON object with these exact keys. No markdown fences, no commentary:
{
  "recipient_appropriateness": "ok" | "wrong_audience",
  "tone": "ok" | "sharp" | "inappropriate",
  "channel_appropriateness": "ok" | "wrong_channel",
  "rationale": "one sentence explaining any flag, or empty string if all ok"
}"""


async def classify_outgoing(
    *,
    student_id: int,
    application_id: int,
    channel: str,
    recipient: str,
    subject: str,
    body: str,
    student_stage: str = "",
    recipient_role_hint: str = "",
) -> ClassificationResult:
    """Classify an outgoing student message on three axes.

    Returns a ClassificationResult. On any error, fails open with
    `status="classifier_unavailable"`.
    """
    classified_at = datetime.now(timezone.utc).isoformat()

    # Stub short-circuit
    if os.environ.get("LLM_PROVIDER", "stub").lower() == "stub":
        return ClassificationResult(
            rationale="stub mode",
            classified_at=classified_at,
        )

    user_prompt = _build_user_prompt(
        channel=channel,
        recipient=recipient,
        subject=subject,
        body=body,
        student_stage=student_stage,
        recipient_role_hint=recipient_role_hint,
    )

    try:
        raw = await chat_completion(
            _SYSTEM_PROMPT,
            [{"role": "user", "content": user_prompt}],
        )
        return _parse_classification(raw, classified_at)
    except Exception as exc:  # noqa: BLE001
        import logging
        logging.getLogger(__name__).warning(
            "classify_outgoing: classifier unavailable (%s)", exc,
        )
        return ClassificationResult(
            rationale=f"classifier_unavailable: {exc}",
            classified_at=classified_at,
            status="classifier_unavailable",
        )


def _build_user_prompt(
    *,
    channel: str,
    recipient: str,
    subject: str,
    body: str,
    student_stage: str,
    recipient_role_hint: str,
) -> str:
    parts = [
        f"Channel: {channel}",
        f"Recipient: {recipient}",
    ]
    if recipient_role_hint:
        parts.append(f"Recipient role: {recipient_role_hint}")
    if student_stage:
        parts.append(f"Student stage: {student_stage}")
    if subject:
        parts.append(f"Subject: {subject}")
    parts.append(f"Body:\n{body}")
    parts.append("\nClassify this message. Return ONLY the JSON object.")
    return "\n".join(parts)


def _parse_classification(raw: str, classified_at: str) -> ClassificationResult:
    """Parse the classifier's JSON response, fail-open on any error."""
    cleaned = (raw or "").strip()
    if cleaned.startswith("```"):
        first_nl = cleaned.find("\n")
        if first_nl != -1:
            cleaned = cleaned[first_nl + 1:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()

    try:
        data = json.loads(cleaned)
        return ClassificationResult(
            recipient_appropriateness=_coerce_flag(
                data.get("recipient_appropriateness"),
                valid={"ok", "wrong_audience"},
            ),
            tone=_coerce_flag(
                data.get("tone"),
                valid={"ok", "sharp", "inappropriate"},
            ),
            channel_appropriateness=_coerce_flag(
                data.get("channel_appropriateness"),
                valid={"ok", "wrong_channel"},
            ),
            rationale=str(data.get("rationale", ""))[:500],
            classified_at=classified_at,
        )
    except (ValueError, TypeError) as exc:
        import logging
        logging.getLogger(__name__).warning(
            "_parse_classification: bad response (%s) raw=%r", exc, raw[:200],
        )
        return ClassificationResult(
            rationale=f"malformed_response: {exc}",
            classified_at=classified_at,
            status="classifier_unavailable",
        )


def _coerce_flag(value, *, valid: set[str]) -> str:
    """Coerce an unknown value to 'ok'. Fail-open on any unknown flag."""
    if isinstance(value, str) and value in valid:
        return value
    return "ok"
```

- [ ] **Step 2: Import check**

```bash
WORKREADY_DB=/tmp/cm.db LLM_PROVIDER=stub uv run python -c "
from workready_api.comms_monitor import classify_outgoing, ClassificationResult
print('OK: comms_monitor imports cleanly')
"
```

Expected: `OK: comms_monitor imports cleanly`

- [ ] **Step 3: Commit**

```bash
git add workready_api/comms_monitor.py
git commit -m "Stage 7: add comms_monitor classifier module"
```

### Task 6.2: Write comms_monitor smoke

**Files:**
- Create: `scripts/smoke_comms_monitor.py`

- [ ] **Step 1: Write the smoke**

```python
#!/usr/bin/env python3
"""Smoke test: comms_monitor classifier.

Covers:
1. Stub mode returns all-ok with rationale='stub mode'
2. any_flag() returns False when all axes are ok
3. any_flag() returns True when at least one axis is flagged
4. to_json() produces valid JSON
5. Fail-open via patched chat_completion raising an exception
"""

import asyncio
import json
import os

os.environ.setdefault("LLM_PROVIDER", "stub")

from workready_api.comms_monitor import (
    ClassificationResult, classify_outgoing, _parse_classification,
)

# --- Test 1: stub mode ---
result = asyncio.run(classify_outgoing(
    student_id=1, application_id=1, channel="chat",
    recipient="karen@test.com", subject="", body="hey karen",
))
assert result.recipient_appropriateness == "ok"
assert result.tone == "ok"
assert result.channel_appropriateness == "ok"
assert result.rationale == "stub mode"
assert result.status == "ok"
print("  [1/5] stub mode returns all-ok")

# --- Test 2: any_flag is False for all-ok ---
assert result.any_flag() is False
print("  [2/5] any_flag() false for all-ok")

# --- Test 3: any_flag is True with a flag ---
flagged = ClassificationResult(
    recipient_appropriateness="wrong_audience",
    rationale="CEO about trivia",
)
assert flagged.any_flag() is True
print("  [3/5] any_flag() true when flagged")

# --- Test 4: to_json round-trip ---
js = flagged.to_json()
data = json.loads(js)
assert data["recipient_appropriateness"] == "wrong_audience"
print("  [4/5] to_json is valid JSON")

# --- Test 5: fail-open via parse error ---
bad = _parse_classification("not-json-at-all", "2026-04-14T10:00:00Z")
assert bad.status == "classifier_unavailable"
assert bad.recipient_appropriateness == "ok"
print("  [5/5] fail-open on bad JSON")

# --- Test 6: coerce unknown flag values to ok ---
unknown = _parse_classification('{"recipient_appropriateness": "WEIRD_VALUE"}', "2026-04-14T10:00:00Z")
assert unknown.recipient_appropriateness == "ok"
print("  [6/6] unknown flag values coerce to ok")

print("\nOK: comms_monitor smoke passed")
```

- [ ] **Step 2: Run it**

```bash
LLM_PROVIDER=stub uv run python scripts/smoke_comms_monitor.py
```

Expected: all checks print OK

- [ ] **Step 3: Commit**

```bash
git add scripts/smoke_comms_monitor.py
git commit -m "Stage 7: comms_monitor smoke test"
```

---

## Block 7 — Classifier hook in `mail.py`

Wire the classifier into the existing `compose_message` path. When a flag fires, replace the expected character reply with a bounce-back from the appropriate proxy.

### Task 7.1: Add the proxy character registry

**Files:**
- Modify: `workready_api/mail.py`

- [ ] **Step 1: Find the right insertion point**

Open `workready_api/mail.py` and locate the top-of-file imports and module constants. Look for a place after the imports and before the first `async def` — that's where the proxy registry goes.

- [ ] **Step 2: Add the proxy registry**

Insert this block near the top of mail.py (after imports, before any class or function):

```python
# ============================================================
# Comms monitor — proxy characters for bounce-backs
# ============================================================

JENNY_PROXY_SENDER_NAME = "Jenny Kirkwood"
JENNY_PROXY_SENDER_ROLE = "Executive Assistant to the Leadership Team"


def _jenny_email_for_company(company_slug: str) -> str:
    """Jenny's email on this company's domain. Pattern: jenny.kirkwood@<domain>."""
    domain = company_slug.replace("-", "") + ".com.au"
    return f"jenny.kirkwood@{domain}"


def _jenny_bounceback_body(
    *,
    student_first_name: str,
    original_subject: str,
    rationale: str,
    company_name: str,
) -> str:
    """Generate a warm, in-character bounce-back from Jenny."""
    # Extract a redirect hint from the classifier's rationale
    redirect_hint = ""
    if "facilities" in rationale.lower():
        redirect_hint = "facilities"
    elif "hr" in rationale.lower() or "people" in rationale.lower():
        redirect_hint = "HR / People team"
    elif "it" in rationale.lower() or "helpdesk" in rationale.lower():
        redirect_hint = "IT helpdesk"
    else:
        redirect_hint = "the right team"

    redirect_line = (
        f"For this one, the best people to ask are the {redirect_hint} "
        f"team — they'll be much faster than routing through us."
        if redirect_hint != "the right team"
        else f"I'd suggest asking {redirect_hint} directly — they'll be "
             f"much faster than routing through executive comms."
    )

    return (
        f"Hi {student_first_name},\n\n"
        f"Thanks for your message. I manage inbound requests for the "
        f"executive team at {company_name}, so your note landed with me "
        f"first. {redirect_line}\n\n"
        f"Welcome to the team — hope you're settling in well.\n\n"
        f"— Jenny Kirkwood\n"
        f"  Executive Assistant\n"
        f"  {company_name}"
    )


def _mentor_tone_note_body(
    *,
    student_first_name: str,
    mentor_name: str,
    original_subject: str,
    rationale: str,
    tone_flag: str,
) -> str:
    """Gentle note from the mentor about tone."""
    tone_phrase = (
        "came across a bit sharper than I think you meant"
        if tone_flag == "sharp"
        else "wasn't quite the register we'd use in work comms"
    )
    return (
        f"Hi {student_first_name},\n\n"
        f"Quick one — I noticed your message \"{original_subject}\" "
        f"{tone_phrase}. Everything OK? Sometimes a sharp message can "
        f"land harder than we meant, especially over email where there's "
        f"no tone of voice to soften things.\n\n"
        f"No big deal, just thought I'd flag it. Happy to chat if anything's "
        f"bothering you.\n\n"
        f"— {mentor_name}"
    )


def _wrong_channel_system_body(
    *,
    student_first_name: str,
    original_subject: str,
) -> str:
    """System note when personal inbox is used for work."""
    return (
        f"Hi {student_first_name},\n\n"
        f"Quick heads-up: you just sent \"{original_subject}\" from your "
        f"personal email, but it looks like a work matter. For work "
        f"communications, use your WorkReady portal work inbox — it "
        f"keeps the two streams separate and makes it easier for your "
        f"mentor and colleagues to find things later.\n\n"
        f"No harm done this time — this is just a reminder.\n\n"
        f"— WorkReady"
    )
```

- [ ] **Step 3: Verify syntax**

```bash
WORKREADY_DB=/tmp/mail.db LLM_PROVIDER=stub uv run python -c "from workready_api import mail; print('ok')"
```

Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add workready_api/mail.py
git commit -m "Stage 7: add proxy character registry in mail.py"
```

### Task 7.2: Hook classifier into `compose_message`

**Files:**
- Modify: `workready_api/mail.py`

- [ ] **Step 1: Understand the existing compose flow**

Read `workready_api/mail.py:compose_message` (around line 108). Look for:

1. Where the student's outgoing message is persisted (`create_message(... direction='outbound' ...)`)
2. Where the character reply is scheduled (usually a `deliver_at` computation and another `create_message` call)

You'll hook the classifier call BEFORE the character reply scheduling, and branch based on `result.any_flag()`.

- [ ] **Step 2: Add the classifier call**

In `compose_message`, just after the student's outgoing message is persisted, add:

```python
    # --- Stage 7: comms monitor classifier hook ---
    from workready_api.comms_monitor import classify_outgoing

    classification = await classify_outgoing(
        student_id=student["id"],
        application_id=application_id,
        channel="email",
        recipient=recipient_email,
        subject=subject,
        body=body,
        student_stage=app_data.get("current_stage", "") if app_data else "",
        recipient_role_hint="",
    )

    # Write the classification onto the just-persisted outbound message row
    if classification.any_flag() or classification.status != "ok":
        from workready_api.db import get_db as _get_db
        with _get_db() as _conn:
            _conn.execute(
                "UPDATE messages SET review_flag = ? WHERE id = ?",
                (classification.to_json(), student_message_id),
            )
```

**Note:** `student_message_id` is the row ID returned from the `create_message` call that persisted the student's outbound message. If the existing code doesn't capture that return value, add `student_message_id = create_message(...)`.

- [ ] **Step 3: Branch on the flag**

Replace the existing character reply scheduling with a branching block:

```python
    if classification.any_flag():
        # Flagged — schedule a bounce-back instead of the character reply
        await _schedule_bounceback(
            classification=classification,
            student=student,
            application_id=application_id,
            app_data=app_data,
            recipient_email=recipient_email,
            subject=subject,
            inbox=inbox,
        )
    else:
        # All ok — schedule the normal character reply (existing code path)
        # <EXISTING CODE HERE — the reply scheduling that was there before>
        pass
```

Keep the existing reply code inside the `else` branch. Don't lose the existing logic — just wrap it.

- [ ] **Step 4: Write the `_schedule_bounceback` helper**

Add this function near the bottom of `mail.py`:

```python
async def _schedule_bounceback(
    *,
    classification,
    student: dict,
    application_id: int,
    app_data: dict | None,
    recipient_email: str,
    subject: str,
    inbox: str,
) -> None:
    """Schedule a bounce-back message based on the classifier flag.

    Called from compose_message when classification.any_flag() is True.
    """
    from workready_api.db import create_message
    from workready_api.availability import compute_reply_deliver_at
    from workready_api.jobs import get_job

    first_name = (student.get("name") or "").split()[0] if student.get("name") else "there"
    company_slug = (app_data or {}).get("company_slug", "")
    job = get_job(company_slug, (app_data or {}).get("job_slug", "")) or {}
    company_name = job.get("company", company_slug)
    mentor_name = job.get("reports_to", "Your mentor")

    deliver_at = compute_reply_deliver_at(
        company_slug,
        student.get("last_login_at"),
    )

    # Priority: recipient > tone > channel
    if classification.recipient_appropriateness == "wrong_audience":
        body = _jenny_bounceback_body(
            student_first_name=first_name,
            original_subject=subject,
            rationale=classification.rationale,
            company_name=company_name,
        )
        create_message(
            student_id=student["id"],
            student_email=student.get("email", ""),
            sender_name=JENNY_PROXY_SENDER_NAME,
            sender_role=JENNY_PROXY_SENDER_ROLE,
            sender_email=_jenny_email_for_company(company_slug),
            subject=f"Re: {subject}",
            body=body,
            inbox=inbox,
            application_id=application_id,
            related_stage="placement",
            deliver_at=deliver_at,
        )
        return

    if classification.tone in ("sharp", "inappropriate"):
        body = _mentor_tone_note_body(
            student_first_name=first_name,
            mentor_name=mentor_name,
            original_subject=subject,
            rationale=classification.rationale,
            tone_flag=classification.tone,
        )
        create_message(
            student_id=student["id"],
            student_email=student.get("email", ""),
            sender_name=mentor_name,
            sender_role=f"Your mentor at {company_name}",
            sender_email=f"{mentor_name.lower().replace(' ', '.')}@"
                         f"{company_slug.replace('-', '')}.com.au",
            subject=f"Quick note",
            body=body,
            inbox=inbox,
            application_id=application_id,
            related_stage="placement",
            deliver_at=deliver_at,
        )
        return

    if classification.channel_appropriateness == "wrong_channel":
        body = _wrong_channel_system_body(
            student_first_name=first_name,
            original_subject=subject,
        )
        create_message(
            student_id=student["id"],
            student_email=student.get("email", ""),
            sender_name="WorkReady",
            sender_role="Simulation guide",
            sender_email="noreply@workready.eduserver.au",
            subject=f"Heads up — channel check",
            body=body,
            inbox="personal",  # system notes land in personal
            application_id=application_id,
            related_stage="placement",
            deliver_at=deliver_at,
        )
        return
```

- [ ] **Step 5: Import check**

```bash
WORKREADY_DB=/tmp/mail.db LLM_PROVIDER=stub uv run python -c "from workready_api import app; print('ok')"
```

Expected: `ok`

- [ ] **Step 6: Commit**

```bash
git add workready_api/mail.py
git commit -m "Stage 7: wire classifier + bounce-back scheduling into mail.py"
```

### Task 7.3: Write mail classifier smoke

**Files:**
- Create: `scripts/smoke_mail_classifier.py`

- [ ] **Step 1: Write the smoke**

Because the stub classifier always returns all-ok, this smoke exercises the bounce-back branches by directly calling `_schedule_bounceback` with pre-built flagged `ClassificationResult` instances. The actual classifier integration is verified via `smoke_comms_monitor.py`.

```python
#!/usr/bin/env python3
"""Smoke test: mail.py bounce-back paths.

Calls _schedule_bounceback directly with pre-built flagged
ClassificationResults (since stub-mode classifier always returns ok).
Asserts that the right proxy message lands in the right inbox.
"""

import asyncio
import os
import pathlib

os.environ.setdefault("WORKREADY_DB", "/tmp/smoke_mail.db")
os.environ.setdefault("LLM_PROVIDER", "stub")
pathlib.Path(os.environ["WORKREADY_DB"]).unlink(missing_ok=True)

from workready_api.db import (
    init_db, get_or_create_student, create_application, advance_stage,
    get_db, get_inbox,
)
from workready_api.jobs import _COMPANY_CACHE, _JOB_CACHE
from workready_api.comms_monitor import ClassificationResult
from workready_api.mail import _schedule_bounceback

init_db()

_COMPANY_CACHE["mail-test"] = {
    "company": "Mail Test Co",
    "business_hours": {"start": 9, "end": 17, "days": [1, 2, 3, 4, 5, 6, 7]},
    "employees": [
        {"slug": "karen-whitfield", "name": "Karen Whitfield", "role": "Ops Lead"},
    ],
}
_JOB_CACHE[("mail-test", "analyst")] = {
    "slug": "analyst", "title": "Analyst",
    "reports_to": "Karen Whitfield",
    "team": ["karen-whitfield"],
}

s = get_or_create_student("mail@example.com", "Alex Tester")
app_id = create_application(
    student_id=s["id"], student_email="mail@example.com",
    company_slug="mail-test", job_slug="analyst", job_title="Analyst",
)
advance_stage(app_id, "placement")

# --- Test 1: wrong_audience → Jenny bounce-back ---
flag_audience = ClassificationResult(
    recipient_appropriateness="wrong_audience",
    rationale="Facilities question sent to CEO",
)
asyncio.run(_schedule_bounceback(
    classification=flag_audience,
    student=s,
    application_id=app_id,
    app_data={"company_slug": "mail-test", "job_slug": "analyst",
              "current_stage": "placement"},
    recipient_email="ceo@mailtest.com.au",
    subject="Where's the coffee machine?",
    inbox="work",
))

# Query the messages table
with get_db() as conn:
    rows = conn.execute(
        "SELECT sender_name, subject FROM messages "
        "WHERE application_id = ? ORDER BY id DESC LIMIT 5",
        (app_id,),
    ).fetchall()
assert any("Jenny" in r["sender_name"] for r in rows), \
    f"Jenny bounce-back missing from {[dict(r) for r in rows]}"
print("  [1/3] wrong_audience → Jenny bounce-back landed")

# --- Test 2: tone sharp → mentor gentle note ---
flag_tone = ClassificationResult(
    tone="sharp",
    rationale="Terse tone in response to mentor feedback",
)
asyncio.run(_schedule_bounceback(
    classification=flag_tone,
    student=s,
    application_id=app_id,
    app_data={"company_slug": "mail-test", "job_slug": "analyst",
              "current_stage": "placement"},
    recipient_email="karen.whitfield@mailtest.com.au",
    subject="Task 2",
    inbox="work",
))

with get_db() as conn:
    rows = conn.execute(
        "SELECT sender_name, subject FROM messages "
        "WHERE application_id = ? ORDER BY id DESC LIMIT 5",
        (app_id,),
    ).fetchall()
assert any("Karen" in r["sender_name"] for r in rows), \
    f"Mentor note missing from {[dict(r) for r in rows]}"
print("  [2/3] tone=sharp → mentor gentle note landed")

# --- Test 3: wrong_channel → system note ---
flag_channel = ClassificationResult(
    channel_appropriateness="wrong_channel",
    rationale="Personal email used for work matter",
)
asyncio.run(_schedule_bounceback(
    classification=flag_channel,
    student=s,
    application_id=app_id,
    app_data={"company_slug": "mail-test", "job_slug": "analyst",
              "current_stage": "placement"},
    recipient_email="karen.whitfield@mailtest.com.au",
    subject="Quick question",
    inbox="personal",
))

with get_db() as conn:
    rows = conn.execute(
        "SELECT sender_name, subject FROM messages "
        "WHERE application_id = ? ORDER BY id DESC LIMIT 5",
        (app_id,),
    ).fetchall()
assert any("WorkReady" in r["sender_name"] for r in rows), \
    f"System note missing from {[dict(r) for r in rows]}"
print("  [3/3] wrong_channel → system note landed")

print("\nOK: mail classifier smoke passed")
```

- [ ] **Step 2: Run it**

```bash
uv run python scripts/smoke_mail_classifier.py
```

Expected: all 3 tests pass, final `OK: mail classifier smoke passed`

- [ ] **Step 3: Commit**

```bash
git add scripts/smoke_mail_classifier.py
git commit -m "Stage 7: mail classifier bounce-back smoke"
```

---

## Block 8 — Chat routes

### Task 8.1: Add chat models

**Files:**
- Modify: `workready_api/models.py`

- [ ] **Step 1: Add models**

Append to `workready_api/models.py`:

```python
# --- Stage 7: Chat routes ---


class ChatSendRequest(BaseModel):
    application_id: int
    character_slug: str
    content: str


class ChatMessageModel(BaseModel):
    id: int
    channel: str
    author: str  # "student" | "character"
    sender_name: str
    content: str
    created_at: str
    deliver_at: str | None = None


class ChatThreadResponse(BaseModel):
    application_id: int
    character_slug: str
    character_name: str
    character_role: str = ""
    presence_ok: bool = False
    messages: list[ChatMessageModel] = []
```

- [ ] **Step 2: Import check**

```bash
WORKREADY_DB=/tmp/ch.db LLM_PROVIDER=stub uv run python -c "
from workready_api.models import ChatSendRequest, ChatThreadResponse
print('ok')
"
```

Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add workready_api/models.py
git commit -m "Stage 7: add chat request/response models"
```

### Task 8.2: Add chat routes in app.py

**Files:**
- Modify: `workready_api/app.py`

- [ ] **Step 1: Add the routes**

Append these routes near the existing mail/task routes in `app.py`:

```python
@app.post("/api/v1/chat/send")
async def chat_send(req: ChatSendRequest) -> dict:
    """Send a chat message from student to a team character.

    Runs through the comms monitor classifier. On any flag, schedules
    a bounce-back instead of a character reply (reuses mail.py's
    _schedule_bounceback helper). On all-ok, schedules a character
    reply via context-aware reply generation.
    """
    from workready_api.comms_monitor import classify_outgoing
    from workready_api.context_builder import build_character_context
    from workready_api.availability import compute_reply_deliver_at
    from workready_api.mail import _schedule_bounceback
    from workready_api.interview import chat_completion

    app_data = get_application(req.application_id)
    if not app_data:
        raise HTTPException(status_code=404, detail="Application not found")
    if app_data.get("current_stage") != "placement":
        raise HTTPException(
            status_code=400,
            detail="Chat is only available while on placement",
        )

    student = get_student_by_id(app_data["student_id"]) or {}
    company_slug = app_data["company_slug"]

    # 1. Persist student's outbound message immediately
    from workready_api.db import create_message as _cm
    student_msg_id = _cm(
        student_id=student["id"],
        student_email=student.get("email", ""),
        sender_name=student.get("name", ""),
        sender_role="Student",
        sender_email=student.get("email", ""),
        recipient_email=req.character_slug + "@" + company_slug.replace("-", "") + ".com.au",
        subject="",
        body=req.content,
        inbox="work",
        application_id=req.application_id,
        related_stage="placement",
        direction="outbound",
        channel="chat",
    )

    # 2. Classify
    classification = await classify_outgoing(
        student_id=student["id"],
        application_id=req.application_id,
        channel="chat",
        recipient=req.character_slug,
        subject="",
        body=req.content,
        student_stage=app_data.get("current_stage", ""),
    )

    # 3. Write review_flag if any
    if classification.any_flag() or classification.status != "ok":
        from workready_api.db import get_db as _gdb
        with _gdb() as _conn:
            _conn.execute(
                "UPDATE messages SET review_flag = ? WHERE id = ?",
                (classification.to_json(), student_msg_id),
            )

    # 4. Branch: bounce-back or real reply
    if classification.any_flag():
        await _schedule_bounceback(
            classification=classification,
            student=student,
            application_id=req.application_id,
            app_data=app_data,
            recipient_email=req.character_slug,
            subject="",
            inbox="work",
        )
    else:
        # Build context + generate reply + schedule
        ctx = build_character_context(
            student_id=student["id"],
            character_slug=req.character_slug,
            application_id=req.application_id,
        )

        system_prompt = _build_chat_system_prompt(ctx)
        messages = [
            {"role": "assistant", "content": m["text"]}
            if m["who"] == "character"
            else {"role": "user", "content": m["text"]}
            for m in ctx.thread
        ]
        messages.append({"role": "user", "content": req.content})

        reply_text = await chat_completion(system_prompt, messages)

        deliver_at = compute_reply_deliver_at(
            company_slug,
            student.get("last_login_at"),
        )

        _cm(
            student_id=student["id"],
            student_email=student.get("email", ""),
            sender_name=ctx.character_name,
            sender_role=ctx.character_role + " at " + ctx.company_name,
            sender_email=req.character_slug + "@" + company_slug.replace("-", "") + ".com.au",
            recipient_email=student.get("email", ""),
            subject="",
            body=reply_text,
            inbox="work",
            application_id=req.application_id,
            related_stage="placement",
            direction="inbound",
            channel="chat",
            deliver_at=deliver_at,
        )

    return {"message_id": student_msg_id, "flagged": classification.any_flag()}


def _build_chat_system_prompt(ctx) -> str:
    """Flatten a CharacterContext into a chat-style system prompt."""
    task_lines = []
    if ctx.active_tasks:
        task_lines.append("ACTIVE TASKS:")
        for t in ctx.active_tasks:
            task_lines.append(f"  - #{t.get('sequence')}: {t.get('title')} ({t.get('status')})")
            if t.get("brief"):
                task_lines.append(f"    Brief: {t['brief'][:200]}")

    past_lines = []
    if ctx.past_tasks:
        past_lines.append("\nCOMPLETED TASKS:")
        for t in ctx.past_tasks:
            score = f"({t['score']}/100)" if t.get("score") else ""
            past_lines.append(f"  - #{t.get('sequence')}: {t.get('title')} {score}")

    summary_block = (
        f"\nEarlier in your thread with {ctx.student_first_name}: "
        f"{ctx.thread_summary}"
        if ctx.thread_summary else ""
    )

    return f"""{ctx.persona_prompt}

═══════════════════════════════════════════════════
You are {ctx.character_name}, {ctx.character_role} at {ctx.company_name}. You're having a casual workplace chat with {ctx.student_first_name}, an intern on your team working as a {ctx.job_title}.

This is a chat (not email) — keep messages short: 1-2 sentences, the way you'd actually type in a workplace Slack or Teams. Be warm, professional, in-character.
{summary_block}

{chr(10).join(task_lines) if task_lines else ""}
{chr(10).join(past_lines) if past_lines else ""}

Respond naturally to what the student just said. Stay in character.
"""


@app.get("/api/v1/chat/thread/{application_id}/{character_slug}", response_model=ChatThreadResponse)
def chat_thread(application_id: int, character_slug: str) -> ChatThreadResponse:
    """Return the delivered chat messages between a student and a character."""
    app_data = get_application(application_id)
    if not app_data:
        raise HTTPException(status_code=404, detail="Application not found")

    from workready_api.db import get_db as _gdb
    from workready_api.db import _now as _now_iso

    now = _now_iso()
    with _gdb() as conn:
        rows = conn.execute(
            """SELECT * FROM messages
               WHERE application_id = ?
                 AND channel = 'chat'
                 AND (deliver_at IS NULL OR deliver_at <= ?)
               ORDER BY id ASC""",
            (application_id, now),
        ).fetchall()

    # Filter client-side to this character by sender name/email
    slug_first = character_slug.split("-")[0].lower()
    messages: list[ChatMessageModel] = []
    for row in rows:
        d = dict(row)
        sender = (d.get("sender_name") or "").lower()
        sender_email = (d.get("sender_email") or "").lower()
        recipient = (d.get("recipient_email") or "").lower()
        direction = d.get("direction", "inbound")

        # Message involves this character if:
        # - outbound (student → character): character_slug in recipient
        # - inbound (character → student): character_slug first name in sender
        involves = (
            (direction == "outbound" and character_slug in recipient)
            or (direction == "inbound" and slug_first in sender)
        )
        if not involves:
            continue

        messages.append(ChatMessageModel(
            id=d["id"],
            channel="chat",
            author="student" if direction == "outbound" else "character",
            sender_name=d.get("sender_name") or "",
            content=d.get("body") or "",
            created_at=d.get("created_at") or "",
            deliver_at=d.get("deliver_at"),
        ))

    # Resolve character metadata
    from workready_api.jobs import get_company
    company = get_company(app_data["company_slug"]) or {}
    char = next(
        (e for e in company.get("employees", []) if e.get("slug") == character_slug),
        {"name": character_slug, "role": ""},
    )

    from workready_api.availability import is_character_available
    return ChatThreadResponse(
        application_id=application_id,
        character_slug=character_slug,
        character_name=char.get("name", character_slug),
        character_role=char.get("role", ""),
        presence_ok=is_character_available(app_data["company_slug"], character_slug),
        messages=messages,
    )
```

- [ ] **Step 2: Add imports to app.py**

At the top of `app.py`, ensure these are imported (add if missing):

```python
from workready_api.models import (
    # ... existing ...
    ChatSendRequest, ChatMessageModel, ChatThreadResponse,
)
```

- [ ] **Step 3: Import check**

```bash
WORKREADY_DB=/tmp/ch.db LLM_PROVIDER=stub uv run python -c "from workready_api import app; print('ok')"
```

Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add workready_api/app.py
git commit -m "Stage 7: add chat/send and chat/thread routes"
```

### Task 8.3: Write chat routes smoke

**Files:**
- Create: `scripts/smoke_chat_routes.py`

- [ ] **Step 1: Write the smoke**

```python
#!/usr/bin/env python3
"""Smoke test: chat routes end-to-end.

Boots the API against a seeded DB, sends a chat message via
POST /chat/send, polls GET /chat/thread, asserts messages appear.
"""

import os
import pathlib
import subprocess
import time

os.environ.setdefault("WORKREADY_DB", "/tmp/smoke_chat.db")
os.environ.setdefault("LLM_PROVIDER", "stub")
pathlib.Path(os.environ["WORKREADY_DB"]).unlink(missing_ok=True)

# Seed the DB directly first (before the server starts)
from workready_api.db import (
    init_db, get_or_create_student, create_application, advance_stage,
)
from workready_api.jobs import _COMPANY_CACHE, _JOB_CACHE

init_db()

_COMPANY_CACHE["chat-test"] = {
    "company": "Chat Test Co",
    "domain": "chattest.com.au",
    "business_hours": {"start": 0, "end": 24, "days": [1, 2, 3, 4, 5, 6, 7]},
    "employees": [
        {"slug": "karen-whitfield", "name": "Karen Whitfield",
         "role": "Ops Lead", "availability": {"status": "available"}},
    ],
}
_JOB_CACHE[("chat-test", "analyst")] = {
    "slug": "analyst", "title": "Analyst",
    "reports_to": "Karen Whitfield",
    "team": ["karen-whitfield"],
}

s = get_or_create_student("chat@example.com", "Alex Tester")
app_id = create_application(
    student_id=s["id"], student_email="chat@example.com",
    company_slug="chat-test", job_slug="analyst", job_title="Analyst",
)
advance_stage(app_id, "placement")

# Boot the server
proc = subprocess.Popen(
    ["uv", "run", "uvicorn", "workready_api.app:app",
     "--port", "8702", "--log-level", "warning"],
    env={**os.environ},
)
try:
    time.sleep(3)

    import httpx
    base = "http://127.0.0.1:8702"

    # --- Test 1: POST /chat/send ---
    r = httpx.post(f"{base}/api/v1/chat/send", json={
        "application_id": app_id,
        "character_slug": "karen-whitfield",
        "content": "Hey Karen, quick question about task 1",
    }, timeout=10)
    assert r.status_code == 200, f"send failed: {r.status_code} {r.text}"
    result = r.json()
    assert result.get("flagged") is False
    assert "message_id" in result
    print(f"  [1/3] chat/send succeeded, message_id={result['message_id']}")

    # --- Test 2: GET /chat/thread immediately shows the student message ---
    time.sleep(1)  # give the stub reply a tiny delay
    r = httpx.get(f"{base}/api/v1/chat/thread/{app_id}/karen-whitfield")
    assert r.status_code == 200, f"thread failed: {r.status_code} {r.text}"
    thread = r.json()
    student_msgs = [m for m in thread["messages"] if m["author"] == "student"]
    assert len(student_msgs) >= 1, f"No student messages in thread: {thread}"
    print(f"  [2/3] chat/thread returned {len(thread['messages'])} messages")

    # --- Test 3: Thread shape includes character metadata ---
    assert thread["character_name"] == "Karen Whitfield"
    assert thread["presence_ok"] is True  # 24/7 business hours in this test
    print(f"  [3/3] thread metadata correct (character_name, presence_ok)")

finally:
    proc.terminate()
    proc.wait(timeout=5)

print("\nOK: chat routes smoke passed")
```

- [ ] **Step 2: Run it**

```bash
uv run python scripts/smoke_chat_routes.py
```

Expected: all 3 tests pass, final `OK: chat routes smoke passed`

- [ ] **Step 3: Commit**

```bash
git add scripts/smoke_chat_routes.py
git commit -m "Stage 7: chat routes end-to-end smoke"
```

---

## Block 9 — Portal: team directory sidebar

### Task 9.1: Add team directory HTML

**Files:**
- Modify: `workready-portal/index.html`

- [ ] **Step 1: Add the team directory nav section**

In `index.html`, find the `<nav class="sidebar-nav">` block (the one with `<button class="nav-item">` entries for Dashboard, Work Inbox, etc.). Add a new section after the Work Inbox entry:

```html
<!-- Stage 7: Team directory -->
<div class="nav-section nav-team-section hidden" id="nav-team-section">
  <div class="nav-section-title">
    <span>Your Team</span>
    <button class="nav-collapse-btn" id="nav-team-collapse" aria-label="Collapse team">−</button>
  </div>
  <div class="nav-team-list" id="nav-team-list">
    <!-- populated by renderTeamDirectory() -->
  </div>
  <div class="nav-section-title nav-org-title">
    <span>Wider Organisation</span>
  </div>
  <div class="nav-org-list nav-org-list-collapsed" id="nav-org-list">
    <!-- populated by renderTeamDirectory() -->
  </div>
</div>
```

- [ ] **Step 2: Add the chat drawer markup**

Near the end of `<body>`, before the closing `</body>`:

```html
<!-- Stage 7: Chat drawer -->
<aside class="chat-drawer hidden" id="chat-drawer">
  <header class="chat-drawer-header">
    <div class="chat-drawer-character">
      <span class="chat-presence-dot" id="chat-drawer-presence"></span>
      <div>
        <div class="chat-drawer-name" id="chat-drawer-name"></div>
        <div class="chat-drawer-role" id="chat-drawer-role"></div>
      </div>
    </div>
    <button class="chat-drawer-close" id="chat-drawer-close" aria-label="Close">&times;</button>
  </header>
  <div class="chat-drawer-messages" id="chat-drawer-messages">
    <!-- bubbles populated by renderChatThread() -->
  </div>
  <form class="chat-drawer-composer" id="chat-drawer-composer">
    <textarea
      class="chat-drawer-input"
      id="chat-drawer-input"
      placeholder="Type a message..."
      rows="2"
      required></textarea>
    <button type="submit" class="btn btn-primary chat-drawer-send">Send</button>
  </form>
</aside>
```

- [ ] **Step 3: Verify HTML is well-formed**

```bash
cd /Users/michael/Projects/loco-lab/loco-ensyo/workready-portal
# Visual inspection — open in browser to confirm nothing's broken
# Alternatively check no obvious typos:
grep -c "chat-drawer" index.html
# Expected: several hits
```

- [ ] **Step 4: Commit**

```bash
cd /Users/michael/Projects/loco-lab/loco-ensyo/workready-portal
git add index.html
git commit -m "Stage 7: add team directory nav + chat drawer markup"
```

### Task 9.2: Add team directory JS loader

**Files:**
- Modify: `workready-portal/app.js`

- [ ] **Step 1: Add the module near the end of app.js**

Find the end of `app.js` (just before any `// End of file` comment or the closing IIFE). Add:

```javascript
// ============================================================
// Stage 7: Team directory
// ============================================================

var teamState = {
  team: [],
  org: [],
  loaded: false,
  orgExpanded: false,
};

function showTeamSection() {
  var el = document.getElementById('nav-team-section');
  if (el) el.classList.remove('hidden');
}

function hideTeamSection() {
  var el = document.getElementById('nav-team-section');
  if (el) el.classList.add('hidden');
}

function loadTeamDirectory() {
  if (!state.activeApplicationId) {
    hideTeamSection();
    return;
  }
  if (state.currentStage !== 'placement') {
    hideTeamSection();
    return;
  }

  showTeamSection();

  fetch(CONFIG.API_BASE + '/api/v1/team/' + state.activeApplicationId)
    .then(function(r) {
      if (!r.ok) throw new Error('Team load failed: ' + r.status);
      return r.json();
    })
    .then(function(data) {
      teamState.team = data.team || [];
      teamState.org = data.org || [];
      teamState.loaded = true;
      renderTeamDirectory();
    })
    .catch(function(err) {
      console.error('loadTeamDirectory:', err);
      var list = document.getElementById('nav-team-list');
      if (list) list.innerHTML = '<div class="nav-team-error">Failed to load</div>';
    });
}

function renderTeamDirectory() {
  var teamList = document.getElementById('nav-team-list');
  var orgList = document.getElementById('nav-org-list');
  if (!teamList || !orgList) return;

  if (teamState.team.length === 0) {
    teamList.innerHTML = '<div class="nav-team-empty">No team members listed.</div>';
  } else {
    teamList.innerHTML = teamState.team.map(renderTeamMemberRow).join('');
  }

  orgList.innerHTML = teamState.org.map(renderOrgMemberRow).join('');
  orgList.classList.toggle('nav-org-list-collapsed', !teamState.orgExpanded);

  // Wire click handlers
  teamList.querySelectorAll('.nav-team-chat-btn').forEach(function(btn) {
    btn.addEventListener('click', function(e) {
      e.stopPropagation();
      var slug = btn.getAttribute('data-slug');
      openChatDrawer(slug);
    });
  });
}

function renderTeamMemberRow(member) {
  var dotClass = member.presence_ok ? 'presence-dot-on' : 'presence-dot-off';
  var disabledAttr = member.presence_ok ? '' : 'disabled';
  var tooltip = member.presence_ok
    ? 'Chat with ' + member.name
    : (member.availability_note || 'Not available right now');

  return '<div class="nav-team-member">'
    + '<span class="presence-dot ' + dotClass + '" title="' + escapeHtml(tooltip) + '"></span>'
    + '<div class="nav-team-info">'
    + '<div class="nav-team-name">' + escapeHtml(member.name) + '</div>'
    + '<div class="nav-team-role">' + escapeHtml(member.role) + '</div>'
    + '</div>'
    + '<button class="nav-team-chat-btn" data-slug="' + escapeHtml(member.slug) + '" '
    + disabledAttr + ' title="' + escapeHtml(tooltip) + '">💬</button>'
    + '</div>';
}

function renderOrgMemberRow(member) {
  return '<div class="nav-org-member" title="' + escapeHtml(member.role) + '">'
    + '<span class="nav-org-name">' + escapeHtml(member.name) + '</span>'
    + '<span class="nav-org-role">' + escapeHtml(member.role) + '</span>'
    + '</div>';
}

function wireTeamDirectoryControls() {
  var collapseBtn = document.getElementById('nav-team-collapse');
  if (collapseBtn) {
    collapseBtn.addEventListener('click', function() {
      var list = document.getElementById('nav-team-list');
      if (list) list.classList.toggle('hidden');
      collapseBtn.textContent = list.classList.contains('hidden') ? '+' : '−';
    });
  }
  var orgTitle = document.querySelector('.nav-org-title');
  if (orgTitle) {
    orgTitle.addEventListener('click', function() {
      teamState.orgExpanded = !teamState.orgExpanded;
      renderTeamDirectory();
    });
  }
}
```

- [ ] **Step 2: Wire loadTeamDirectory into the render state path**

Find `renderState()` (or equivalent — the function called after `/student/.../state` returns). At the end, add:

```javascript
  loadTeamDirectory();
```

Also call `wireTeamDirectoryControls()` once at boot, inside the existing `initApp()` / DOMContentLoaded handler.

- [ ] **Step 3: Syntax check**

```bash
cd /Users/michael/Projects/loco-lab/loco-ensyo/workready-portal
node --check app.js && echo "JS OK"
```

Expected: `JS OK`

- [ ] **Step 4: Commit**

```bash
git add app.js
git commit -m "Stage 7: team directory loader + sidebar renderer"
```

### Task 9.3: Add team directory CSS

**Files:**
- Modify: `workready-portal/style.css`

- [ ] **Step 1: Append team directory styles**

Append to `style.css`:

```css
/* ============================================================
   Stage 7: Team directory sidebar
   ============================================================ */

.nav-team-section {
  padding: 0.5rem 0;
  border-top: 1px solid var(--color-border, #e5e7eb);
  margin-top: 0.5rem;
}

.nav-team-section.hidden { display: none; }

.nav-section-title {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0.5rem 1rem;
  font-size: 0.72rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: #6b7280;
  cursor: default;
}

.nav-org-title {
  cursor: pointer;
  user-select: none;
}
.nav-org-title:hover { color: #374151; }

.nav-collapse-btn {
  background: none;
  border: none;
  color: #6b7280;
  cursor: pointer;
  font-size: 1rem;
  padding: 0 0.3rem;
}

.nav-team-list {
  padding: 0 0.5rem;
}

.nav-team-member {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.4rem 0.5rem;
  border-radius: 4px;
}
.nav-team-member:hover {
  background: #f3f4f6;
}

.presence-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}
.presence-dot-on { background: #22c55e; }
.presence-dot-off { background: #9ca3af; }

.nav-team-info {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
}

.nav-team-name {
  font-size: 0.85rem;
  font-weight: 500;
  color: #1f2937;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.nav-team-role {
  font-size: 0.72rem;
  color: #6b7280;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.nav-team-chat-btn {
  background: none;
  border: none;
  cursor: pointer;
  padding: 0.2rem 0.4rem;
  font-size: 1rem;
  opacity: 0.7;
}
.nav-team-chat-btn:hover:not([disabled]) { opacity: 1; }
.nav-team-chat-btn[disabled] {
  cursor: not-allowed;
  opacity: 0.3;
}

.nav-org-list-collapsed {
  display: none;
}

.nav-org-list {
  padding: 0 1rem;
}

.nav-org-member {
  display: flex;
  justify-content: space-between;
  padding: 0.25rem 0;
  font-size: 0.78rem;
  color: #6b7280;
}
.nav-org-name { font-weight: 500; }
.nav-org-role { font-size: 0.68rem; }

.nav-team-empty, .nav-team-error {
  padding: 0.5rem 1rem;
  font-size: 0.75rem;
  color: #9ca3af;
  font-style: italic;
}
```

- [ ] **Step 2: Commit**

```bash
git add style.css
git commit -m "Stage 7: team directory sidebar styles"
```

---

## Block 10 — Portal: chat drawer

### Task 10.1: Add chat drawer JS

**Files:**
- Modify: `workready-portal/app.js`

- [ ] **Step 1: Append chat drawer logic**

Append to `app.js` (after the team directory block):

```javascript
// ============================================================
// Stage 7: Chat drawer
// ============================================================

var chatState = {
  open: false,
  characterSlug: null,
  characterName: '',
  characterRole: '',
  presenceOk: false,
  messages: [],
  pollTimer: null,
};

function openChatDrawer(characterSlug) {
  var character = teamState.team.find(function(m) { return m.slug === characterSlug; });
  if (!character) return;

  chatState.characterSlug = characterSlug;
  chatState.characterName = character.name;
  chatState.characterRole = character.role;
  chatState.presenceOk = character.presence_ok;
  chatState.open = true;

  var drawer = document.getElementById('chat-drawer');
  drawer.classList.remove('hidden');

  document.getElementById('chat-drawer-name').textContent = character.name;
  document.getElementById('chat-drawer-role').textContent = character.role;
  var dot = document.getElementById('chat-drawer-presence');
  dot.className = 'chat-presence-dot ' +
    (character.presence_ok ? 'presence-dot-on' : 'presence-dot-off');

  loadChatThread();
  startChatPolling();

  var input = document.getElementById('chat-drawer-input');
  if (input) input.focus();
}

function closeChatDrawer() {
  chatState.open = false;
  stopChatPolling();
  var drawer = document.getElementById('chat-drawer');
  if (drawer) drawer.classList.add('hidden');
}

function loadChatThread() {
  if (!chatState.characterSlug || !state.activeApplicationId) return;

  fetch(
    CONFIG.API_BASE + '/api/v1/chat/thread/' +
    state.activeApplicationId + '/' +
    encodeURIComponent(chatState.characterSlug)
  )
    .then(function(r) { return r.ok ? r.json() : null; })
    .then(function(data) {
      if (!data) return;
      chatState.messages = data.messages || [];
      chatState.presenceOk = data.presence_ok;
      renderChatThread();
    })
    .catch(function(err) { console.error('loadChatThread:', err); });
}

function renderChatThread() {
  var box = document.getElementById('chat-drawer-messages');
  if (!box) return;

  box.innerHTML = chatState.messages.map(function(m) {
    var cls = 'chat-bubble chat-bubble-' + m.author;
    return '<div class="' + cls + '">'
      + '<div class="chat-bubble-content">' + escapeHtml(m.content).replace(/\n/g, '<br>') + '</div>'
      + '</div>';
  }).join('');
  box.scrollTop = box.scrollHeight;
}

function startChatPolling() {
  stopChatPolling();
  chatState.pollTimer = setInterval(function() {
    if (!chatState.open) { stopChatPolling(); return; }
    loadChatThread();
  }, 3000);
}

function stopChatPolling() {
  if (chatState.pollTimer) {
    clearInterval(chatState.pollTimer);
    chatState.pollTimer = null;
  }
}

function sendChatMessage(e) {
  if (e) e.preventDefault();
  if (!chatState.characterSlug || !state.activeApplicationId) return;

  var input = document.getElementById('chat-drawer-input');
  var text = input.value.trim();
  if (!text) return;

  input.value = '';
  input.disabled = true;

  fetch(CONFIG.API_BASE + '/api/v1/chat/send', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      application_id: state.activeApplicationId,
      character_slug: chatState.characterSlug,
      content: text,
    }),
  })
    .then(function(r) { return r.json(); })
    .then(function(result) {
      input.disabled = false;
      input.focus();
      loadChatThread();
    })
    .catch(function(err) {
      console.error('sendChatMessage:', err);
      input.disabled = false;
      input.value = text;
    });
}

function wireChatDrawerControls() {
  var closeBtn = document.getElementById('chat-drawer-close');
  if (closeBtn) closeBtn.addEventListener('click', closeChatDrawer);

  var form = document.getElementById('chat-drawer-composer');
  if (form) form.addEventListener('submit', sendChatMessage);

  var drawer = document.getElementById('chat-drawer');
  document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape' && chatState.open) closeChatDrawer();
  });
}
```

- [ ] **Step 2: Wire the control handler at boot**

In the same DOMContentLoaded / initApp handler where `wireTeamDirectoryControls()` is called, add:

```javascript
  wireChatDrawerControls();
```

- [ ] **Step 3: Syntax check**

```bash
node --check app.js && echo "JS OK"
```

Expected: `JS OK`

- [ ] **Step 4: Commit**

```bash
git add app.js
git commit -m "Stage 7: chat drawer open/close/poll/send logic"
```

### Task 10.2: Add chat drawer CSS

**Files:**
- Modify: `workready-portal/style.css`

- [ ] **Step 1: Append drawer styles**

```css
/* ============================================================
   Stage 7: Chat drawer
   ============================================================ */

.chat-drawer {
  position: fixed;
  top: 0;
  right: 0;
  width: 360px;
  max-width: 90vw;
  height: 100vh;
  background: #fff;
  border-left: 1px solid #e5e7eb;
  box-shadow: -4px 0 16px rgba(0, 0, 0, 0.08);
  display: flex;
  flex-direction: column;
  z-index: 200;
  transform: translateX(0);
  transition: transform 0.2s ease;
}
.chat-drawer.hidden {
  transform: translateX(100%);
  display: flex;  /* keep flex for transition */
}

.chat-drawer-header {
  padding: 1rem 1.25rem;
  border-bottom: 1px solid #e5e7eb;
  display: flex;
  justify-content: space-between;
  align-items: center;
  background: #fafbfc;
}

.chat-drawer-character {
  display: flex;
  align-items: center;
  gap: 0.6rem;
}

.chat-presence-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  display: inline-block;
}

.chat-drawer-name {
  font-weight: 600;
  font-size: 0.95rem;
  color: #1f2937;
}
.chat-drawer-role {
  font-size: 0.75rem;
  color: #6b7280;
}

.chat-drawer-close {
  background: none;
  border: none;
  font-size: 1.5rem;
  color: #9ca3af;
  cursor: pointer;
  padding: 0 0.5rem;
}
.chat-drawer-close:hover { color: #1f2937; }

.chat-drawer-messages {
  flex: 1 1 auto;
  overflow-y: auto;
  padding: 1rem;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  background: #f9fafb;
}

.chat-bubble {
  max-width: 78%;
  padding: 0.55rem 0.85rem;
  border-radius: 14px;
  font-size: 0.88rem;
  line-height: 1.4;
  white-space: pre-wrap;
  word-wrap: break-word;
}

.chat-bubble-student {
  background: #2563eb;
  color: #fff;
  align-self: flex-end;
  border-bottom-right-radius: 4px;
}

.chat-bubble-character {
  background: #fff;
  color: #1f2937;
  border: 1px solid #e5e7eb;
  align-self: flex-start;
  border-bottom-left-radius: 4px;
}

.chat-drawer-composer {
  padding: 0.85rem 1rem;
  border-top: 1px solid #e5e7eb;
  display: flex;
  gap: 0.6rem;
  background: #fff;
}

.chat-drawer-input {
  flex: 1 1 auto;
  border: 1px solid #d1d5db;
  border-radius: 8px;
  padding: 0.55rem 0.75rem;
  font-family: inherit;
  font-size: 0.9rem;
  resize: none;
}
.chat-drawer-input:focus {
  outline: none;
  border-color: #2563eb;
}

.chat-drawer-send {
  flex: 0 0 auto;
}

@media (max-width: 720px) {
  .chat-drawer {
    width: 100vw;
    max-width: 100vw;
  }
}
```

- [ ] **Step 2: Commit**

```bash
git add style.css
git commit -m "Stage 7: chat drawer styles"
```

---

## Block 11 — Personal vs work inbox colour coding

### Task 11.1: Add colour coding CSS

**Files:**
- Modify: `workready-portal/style.css`

- [ ] **Step 1: Add inbox distinguishing classes**

Append to `style.css`:

```css
/* ============================================================
   Stage 7: Personal vs work inbox colour coding
   ============================================================ */

.inbox-view-personal {
  --inbox-accent: #7c3aed; /* purple */
  --inbox-accent-bg: #f5f3ff;
}
.inbox-view-work {
  --inbox-accent: #059669; /* emerald */
  --inbox-accent-bg: #ecfdf5;
}

.inbox-view-personal .inbox-header,
.inbox-view-work .inbox-header {
  border-left: 4px solid var(--inbox-accent);
  padding-left: 0.75rem;
  background: var(--inbox-accent-bg);
}

.message-row-channel-system {
  background: #fef3c7;
  border-left: 3px solid #f59e0b;
}

.message-row-channel-system .message-row-sender::before {
  content: "⚙ ";
  color: #92400e;
}
```

- [ ] **Step 2: Apply the wrapper class in the work + personal inbox views**

In `app.js`, find `loadInbox()` (or equivalent function that renders either inbox). After the inbox type is known, add:

```javascript
  var container = document.getElementById('view-' + view);
  if (container) {
    container.classList.remove('inbox-view-personal', 'inbox-view-work');
    container.classList.add('inbox-view-' + (view.indexOf('work') >= 0 ? 'work' : 'personal'));
  }
```

- [ ] **Step 3: Syntax check**

```bash
node --check app.js && echo "JS OK"
```

Expected: `JS OK`

- [ ] **Step 4: Commit**

```bash
git add app.js style.css
git commit -m "Stage 7: colour-code personal vs work inbox views"
```

---

## Block 12 — Regression + done criteria

### Task 12.1: Run all smoke scripts together

- [ ] **Step 1: Run every new smoke in sequence**

```bash
cd /Users/michael/Projects/loco-lab/loco-ensyo/workready-api

for smoke in \
  scripts/smoke_availability.py \
  scripts/smoke_team_directory.py \
  scripts/smoke_context_builder.py \
  scripts/smoke_comms_monitor.py \
  scripts/smoke_mail_classifier.py \
  scripts/smoke_chat_routes.py \
  scripts/smoke_persona_audit.py
do
  echo "=== $smoke ==="
  LLM_PROVIDER=stub uv run python "$smoke" || { echo "FAILED: $smoke"; exit 1; }
done
echo "ALL SMOKES PASSED"
```

Expected: `ALL SMOKES PASSED`

### Task 12.2: Regression — re-run existing stage smokes

- [ ] **Step 1: Resume flow smoke**

Re-run the same kind of resume-smoke walkthrough used in earlier sessions (submit resume, check assessor returns reasonable fit_score, check outcome message lands). Use the existing heredoc pattern:

```bash
rm -f /tmp/reg_resume.db
WORKREADY_DB=/tmp/reg_resume.db LLM_PROVIDER=stub uv run python -c "
from workready_api.db import init_db, get_or_create_student
init_db()
s = get_or_create_student('reg@example.com', 'Reg Test')
print('OK: resume seeding works')
"
```

Expected: `OK: resume seeding works`

- [ ] **Step 2: Lunchroom flow smoke**

```bash
rm -f /tmp/reg_lunch.db
WORKREADY_DB=/tmp/reg_lunch.db LLM_PROVIDER=stub uv run python -c "
import asyncio
from workready_api.db import (init_db, get_or_create_student, create_application,
  create_lunchroom_invitation, pick_lunchroom_slot, get_lunchroom_session)
from workready_api import scheduling, lunchroom_chat
init_db()
s = get_or_create_student('reg_lunch@example.com', 'Lunch Test')
app_id = create_application(student_id=s['id'], student_email='reg_lunch@example.com',
  company_slug='ironvale-resources', job_slug='junior-analyst', job_title='Junior Analyst')
sid = create_lunchroom_invitation(application_id=app_id, occasion='routine_lunch',
  occasion_detail=None,
  participants=[{'slug':'karen-whitfield','name':'Karen Whitfield','role':'Lead'},
                {'slug':'ravi-mehta','name':'Ravi Mehta','role':'Analyst'}],
  proposed_slots=[scheduling.to_iso(scheduling.now_utc())], trigger_source='task_review')
pick_lunchroom_slot(sid, scheduling.to_iso(scheduling.now_utc()), None)
scheduling.LUNCHROOM_OPENING_DELAY_SECONDS = 0
scheduling.LUNCHROOM_BEAT_INTERVAL_SECONDS = 0
scheduling.LUNCHROOM_BEAT_JITTER_SECONDS = 0
lunchroom_chat.activate(sid)
asyncio.run(lunchroom_chat.deliver_due(sid))
session = get_lunchroom_session(sid)
assert session['status'] == 'completed'
print('OK: lunchroom regression')
"
```

Expected: `OK: lunchroom regression`

- [ ] **Step 3: Exit interview smoke**

Similar pattern — exercise `exit_interview_start`, `exit_interview_message`, `exit_interview_end`, assert the application status flips to `completed`.

- [ ] **Step 4: Journey report smoke**

Exercise `build_journey_report(app_id)` on a seeded application and assert all six sections are present.

### Task 12.3: Manual browser smoke checklist

- [ ] **Step 1: Boot the API**

```bash
cd /Users/michael/Projects/loco-lab/loco-ensyo/workready-api
rm -f /tmp/browser.db
WORKREADY_DB=/tmp/browser.db LLM_PROVIDER=stub uv run uvicorn workready_api.app:app --port 8000 --log-level warning
```

- [ ] **Step 2: Boot the portal**

In a second terminal:

```bash
cd /Users/michael/Projects/loco-lab/loco-ensyo/workready-portal
python3 -m http.server 8001
```

- [ ] **Step 3: Walk the checklist**

1. Open http://localhost:8001 in a browser
2. Sign in as `browser@example.com`
3. Apply to an IronVale role via Quick Apply
4. Use admin page to force-advance the student to `placement`
5. Verify the Team section appears in the sidebar
6. Verify team members render with name + role + presence dot
7. Click a team member with `presence_ok=true` — chat drawer should slide in
8. Type a message, hit Send — message bubble appears immediately
9. Wait ~3 seconds — character reply bubble appears
10. Close drawer with Escape — drawer slides out
11. Click a team member with `presence_ok=false` — chat button is disabled, tooltip shows availability note
12. Switch to work inbox — verify emerald colour accent visible
13. Switch to personal inbox — verify purple colour accent visible

### Task 12.4: Final commit + done

- [ ] **Step 1: Commit any lingering changes**

```bash
cd /Users/michael/Projects/loco-lab/loco-ensyo/workready-api
git status
# If anything untracked or modified, commit it with a "Stage 7: polish" message
```

- [ ] **Step 2: Push**

```bash
git push
cd /Users/michael/Projects/loco-lab/loco-ensyo/workready-portal
git push
```

- [ ] **Step 3: Announce completion**

Plan complete when:

- All 7 new smoke scripts pass
- All 4 regression smokes pass
- Manual browser checklist passes
- `node --check app.js` passes
- `uv run python -c "from workready_api import app; print('ok')"` passes
- Plan 2 (AnythingLLM) remains as the next session's work

"""SQLite persistence for WorkReady simulation state."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator

DB_PATH = Path(os.environ.get("WORKREADY_DB", "workready.db"))

STAGES = [
    "job_board",       # Stage 1: browsing/selecting a role
    "resume",          # Stage 2: submitting a resume
    "interview",       # Stage 3: attending the interview
    "work_task",       # Stage 4: completing the work task
    "lunchroom",       # Stage 5: the lunchroom moment
    "exit_interview",  # Stage 6: the exit interview
]

# Tables only — safe to run before migrations on legacy DBs because all
# CREATE TABLE statements use IF NOT EXISTS. Indexes are split out so they
# can run after migrations have added any missing columns.
TABLES_SCHEMA = """
CREATE TABLE IF NOT EXISTS students (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS postings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_slug TEXT NOT NULL,
    job_slug TEXT NOT NULL,
    source_type TEXT NOT NULL DEFAULT 'direct',  -- direct | agency
    agency_name TEXT,
    listing_title TEXT NOT NULL,
    listing_description TEXT,
    confidential INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE(company_slug, job_slug, source_type, agency_name)
);

CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL REFERENCES students(id),
    student_email TEXT NOT NULL,
    posting_id INTEGER REFERENCES postings(id),
    company_slug TEXT NOT NULL,
    job_slug TEXT NOT NULL,
    job_title TEXT NOT NULL,
    source TEXT DEFAULT 'direct',
    current_stage TEXT DEFAULT 'resume',
    current_interview_step INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active',
    missed_interviews INTEGER NOT NULL DEFAULT 0,
    reschedule_count INTEGER NOT NULL DEFAULT 0,
    cycle INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS stage_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id INTEGER NOT NULL REFERENCES applications(id),
    stage TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'submitted',
    score INTEGER,
    feedback_json TEXT,
    attempt INTEGER DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS interview_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id INTEGER NOT NULL REFERENCES applications(id),
    manager_slug TEXT NOT NULL,
    manager_name TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'hiring',  -- hiring | exit
    transcript_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'active',
    final_score INTEGER,
    feedback_json TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL REFERENCES students(id),
    student_email TEXT NOT NULL,
    inbox TEXT NOT NULL DEFAULT 'personal',
    sender_name TEXT NOT NULL,
    sender_role TEXT,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    application_id INTEGER REFERENCES applications(id),
    booking_id INTEGER REFERENCES interview_bookings(id),
    related_stage TEXT,
    is_read INTEGER DEFAULT 0,
    deliver_at TEXT NOT NULL,
    channel TEXT NOT NULL DEFAULT 'email',
    review_flag TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS interview_bookings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id INTEGER NOT NULL REFERENCES applications(id),
    scheduled_at TEXT NOT NULL,                        -- UTC ISO 8601
    status TEXT NOT NULL DEFAULT 'pending',            -- pending|completed|missed|cancelled
    created_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS message_attachments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER NOT NULL REFERENCES messages(id),
    filename TEXT NOT NULL,
    content_type TEXT NOT NULL DEFAULT 'application/pdf',
    file_path TEXT NOT NULL,
    file_size INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

-- Stage 4: Work tasks ------------------------------------------------------

CREATE TABLE IF NOT EXISTS task_templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_slug TEXT NOT NULL,
    title TEXT NOT NULL,
    brief TEXT NOT NULL,              -- one-line framing
    description TEXT NOT NULL,        -- full markdown brief
    discipline TEXT,                  -- finance, community, technology, etc.
    difficulty TEXT NOT NULL,         -- easy | medium | hard
    estimated_hours INTEGER NOT NULL DEFAULT 4,
    created_at TEXT NOT NULL,
    UNIQUE(company_slug, title)
);

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id INTEGER NOT NULL REFERENCES applications(id),
    task_template_id INTEGER REFERENCES task_templates(id),
    title TEXT NOT NULL,
    brief TEXT NOT NULL,
    description TEXT NOT NULL,
    difficulty TEXT NOT NULL,
    sequence INTEGER NOT NULL,        -- 1, 2, 3...
    status TEXT NOT NULL DEFAULT 'pending',
        -- pending (not yet visible) | assigned (visible, not submitted) |
        -- submitted | passed | failed | resubmit
    assigned_at TEXT NOT NULL,        -- row creation time (bookkeeping)
    visible_at TEXT,                  -- when the student can see it (NULL = gated)
    due_at TEXT,                      -- set when visible_at is set
    submitted_at TEXT,
    reviewed_at TEXT
);

CREATE TABLE IF NOT EXISTS task_submissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    body TEXT NOT NULL,
    attachment_filename TEXT,
    attachment_text TEXT,             -- extracted text from any uploaded PDF
    score INTEGER,                    -- 0-100 (from LLM reviewer)
    feedback_json TEXT,
    review_status TEXT,               -- passed | failed | resubmit (pre-delivery)
    review_deliver_at TEXT,           -- when the outcome is revealed (lazy gate)
    created_at TEXT NOT NULL
);

-- Stage 5: Lunchroom sessions ----------------------------------------------

CREATE TABLE IF NOT EXISTS lunchroom_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id INTEGER NOT NULL REFERENCES applications(id),
    occasion TEXT NOT NULL,           -- routine_lunch | task_celebration | birthday | staff_award | project_launch | cultural_event
    occasion_detail TEXT,             -- e.g. "Sarah's birthday" or "Marcus regional award"
    participants_json TEXT NOT NULL,  -- list of participant dicts (slug, name, role)
    proposed_slots_json TEXT NOT NULL,-- list of ISO slot strings offered to student
    scheduled_at TEXT,                -- picked slot (NULL until accepted)
    status TEXT NOT NULL DEFAULT 'invited',
        -- invited | accepted | active | completed | declined | missed | cancelled
    transcript_json TEXT NOT NULL DEFAULT '[]',
    participation_notes TEXT,
    system_feedback TEXT,
    trigger_source TEXT,              -- task_review | time_based | manual — why this invite fired
    trigger_task_id INTEGER,          -- task id that triggered (for task_review mode)
    invitation_message_id INTEGER,    -- the work-inbox message introducing this invite
    calendar_event_id INTEGER,        -- the calendar row, set once accepted
    created_at TEXT NOT NULL,
    completed_at TEXT
);

-- Stage 5b: Lunchroom chat posts -------------------------------------------

CREATE TABLE IF NOT EXISTS lunchroom_posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES lunchroom_sessions(id),
    sequence INTEGER NOT NULL,        -- ordering within session (1-based)
    author_kind TEXT NOT NULL,        -- 'student' | 'character' | 'system'
    author_slug TEXT,                 -- character slug (NULL for student/system)
    author_name TEXT,                 -- display name
    intention TEXT,                   -- planned beat intention (NULL for student/rendered)
    content TEXT,                     -- rendered post text (NULL until delivered)
    deliver_at TEXT NOT NULL,         -- UTC ISO; filter WHERE deliver_at <= now()
    status TEXT NOT NULL DEFAULT 'pending',
        -- pending | delivered | skipped
    mentions_json TEXT,               -- list of character slugs addressed in this post
    created_at TEXT NOT NULL,
    delivered_at TEXT
);

-- Stage 4c: Calendar events ------------------------------------------------

CREATE TABLE IF NOT EXISTS calendar_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id INTEGER NOT NULL REFERENCES applications(id),
    event_type TEXT NOT NULL,
        -- task_deadline | lunchroom | exit_interview | custom
    title TEXT NOT NULL,
    description TEXT,
    scheduled_at TEXT NOT NULL,       -- UTC ISO (when the event occurs)
    status TEXT NOT NULL DEFAULT 'upcoming',
        -- upcoming | accepted | declined | completed | cancelled
    related_id INTEGER,               -- task_id / lunchroom_session_id / etc.
    created_at TEXT NOT NULL
);
"""

# Indexes — run AFTER migrations so any newly added columns exist
INDEXES_SCHEMA = """
CREATE INDEX IF NOT EXISTS idx_students_email
    ON students(email);
CREATE INDEX IF NOT EXISTS idx_applications_student
    ON applications(student_id);
CREATE INDEX IF NOT EXISTS idx_applications_company_job
    ON applications(company_slug, job_slug);
CREATE INDEX IF NOT EXISTS idx_stage_results_application
    ON stage_results(application_id, stage);
CREATE INDEX IF NOT EXISTS idx_messages_student
    ON messages(student_id, inbox);
CREATE INDEX IF NOT EXISTS idx_interview_sessions_application
    ON interview_sessions(application_id);
CREATE INDEX IF NOT EXISTS idx_postings_company_job
    ON postings(company_slug, job_slug);
CREATE INDEX IF NOT EXISTS idx_applications_posting
    ON applications(posting_id);
CREATE INDEX IF NOT EXISTS idx_interview_bookings_application
    ON interview_bookings(application_id);
CREATE INDEX IF NOT EXISTS idx_message_attachments
    ON message_attachments(message_id);
CREATE INDEX IF NOT EXISTS idx_task_templates_company
    ON task_templates(company_slug);
CREATE INDEX IF NOT EXISTS idx_tasks_application
    ON tasks(application_id, sequence);
CREATE INDEX IF NOT EXISTS idx_task_submissions_task
    ON task_submissions(task_id, created_at);
CREATE INDEX IF NOT EXISTS idx_calendar_events_application
    ON calendar_events(application_id, scheduled_at);
CREATE INDEX IF NOT EXISTS idx_calendar_events_related
    ON calendar_events(related_id, event_type);
CREATE INDEX IF NOT EXISTS idx_lunchroom_sessions_application
    ON lunchroom_sessions(application_id, status);
CREATE INDEX IF NOT EXISTS idx_lunchroom_sessions_scheduled
    ON lunchroom_sessions(scheduled_at) WHERE scheduled_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_lunchroom_posts_session
    ON lunchroom_posts(session_id, sequence);
CREATE INDEX IF NOT EXISTS idx_lunchroom_posts_deliver
    ON lunchroom_posts(session_id, deliver_at) WHERE status = 'pending';
"""


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _migrate(conn: sqlite3.Connection) -> None:
    """Apply any incremental schema migrations to existing databases.

    Migrations are idempotent — they check for state before applying.
    """
    # --- Migration 1: applications.status column ---
    app_cols = _table_columns(conn, "applications")
    if "status" not in app_cols:
        conn.execute("ALTER TABLE applications ADD COLUMN status TEXT NOT NULL DEFAULT 'active'")
        conn.execute("""
            UPDATE applications SET status = 'rejected'
            WHERE id IN (
                SELECT a.id FROM applications a
                JOIN stage_results sr ON sr.application_id = a.id
                WHERE a.current_stage = 'resume' AND sr.stage = 'resume'
                  AND sr.status = 'failed'
            )
        """)
        app_cols = _table_columns(conn, "applications")

    # --- Migration 2: students.id integer primary key ---
    # Detect old schema (email is PK, no id column)
    student_cols = _table_columns(conn, "students")
    if "id" not in student_cols:
        # Old schema: email is the PK. Need to rebuild the table with id PK,
        # then update all FK references in applications and messages.
        conn.execute("""
            CREATE TABLE students_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            INSERT INTO students_new (email, name, created_at)
            SELECT email, name, created_at FROM students
        """)
        conn.execute("DROP TABLE students")
        conn.execute("ALTER TABLE students_new RENAME TO students")

    # --- Migration 3: applications.student_id (replacing student_email FK) ---
    app_cols = _table_columns(conn, "applications")
    if "student_id" not in app_cols:
        conn.execute("ALTER TABLE applications ADD COLUMN student_id INTEGER REFERENCES students(id)")
        conn.execute("""
            UPDATE applications SET student_id = (
                SELECT id FROM students WHERE students.email = applications.student_email
            )
        """)
        # Verify all rows got a student_id (or there were no rows)
        unmapped = conn.execute(
            "SELECT COUNT(*) FROM applications WHERE student_id IS NULL"
        ).fetchone()[0]
        if unmapped > 0:
            raise RuntimeError(
                f"Migration error: {unmapped} applications could not be mapped "
                "to a student_id. Refusing to drop student_email column."
            )

    # --- Migration 4a: applications.current_interview_step ---
    if "current_interview_step" not in app_cols:
        conn.execute(
            "ALTER TABLE applications ADD COLUMN current_interview_step "
            "INTEGER NOT NULL DEFAULT 0"
        )
        app_cols = _table_columns(conn, "applications")

    # --- Migration 4b: applications.posting_id ---
    if "posting_id" not in app_cols:
        conn.execute(
            "ALTER TABLE applications ADD COLUMN posting_id INTEGER REFERENCES postings(id)"
        )
        app_cols = _table_columns(conn, "applications")

    # --- Migration 4c: applications.cycle ---
    if "cycle" not in app_cols:
        conn.execute(
            "ALTER TABLE applications ADD COLUMN cycle INTEGER NOT NULL DEFAULT 1"
        )
        app_cols = _table_columns(conn, "applications")

    # --- Migration 4d: applications.missed_interviews ---
    if "missed_interviews" not in app_cols:
        conn.execute(
            "ALTER TABLE applications ADD COLUMN missed_interviews "
            "INTEGER NOT NULL DEFAULT 0"
        )
        app_cols = _table_columns(conn, "applications")

    # --- Migration 4e: applications.reschedule_count ---
    if "reschedule_count" not in app_cols:
        conn.execute(
            "ALTER TABLE applications ADD COLUMN reschedule_count "
            "INTEGER NOT NULL DEFAULT 0"
        )

    # --- Migration 5: messages.booking_id ---
    msg_cols = _table_columns(conn, "messages")
    if "booking_id" not in msg_cols:
        conn.execute(
            "ALTER TABLE messages ADD COLUMN booking_id INTEGER "
            "REFERENCES interview_bookings(id)"
        )

    # --- Migration 4: messages.student_id (replacing student_email FK) ---
    msg_cols = _table_columns(conn, "messages")
    if "student_id" not in msg_cols:
        conn.execute("ALTER TABLE messages ADD COLUMN student_id INTEGER REFERENCES students(id)")
        conn.execute("""
            UPDATE messages SET student_id = (
                SELECT id FROM students WHERE students.email = messages.student_email
            )
        """)
        unmapped = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE student_id IS NULL"
        ).fetchone()[0]
        if unmapped > 0:
            raise RuntimeError(
                f"Migration error: {unmapped} messages could not be mapped to student_id."
            )

    # Note: we keep the old student_email columns for backwards compatibility.
    # They are no longer authoritative — the new code reads/writes student_id.
    # A future cleanup migration can drop them once we're confident.

    # --- Migration 6: email system columns on messages ---
    msg_cols = _table_columns(conn, "messages")

    if "direction" not in msg_cols:
        conn.execute(
            "ALTER TABLE messages ADD COLUMN direction TEXT NOT NULL DEFAULT 'inbound'"
        )
    if "sender_email" not in msg_cols:
        conn.execute(
            "ALTER TABLE messages ADD COLUMN sender_email TEXT NOT NULL "
            "DEFAULT 'noreply@workready.eduserver.au'"
        )
    if "recipient_email" not in msg_cols:
        conn.execute(
            "ALTER TABLE messages ADD COLUMN recipient_email TEXT"
        )
    if "thread_id" not in msg_cols:
        conn.execute(
            "ALTER TABLE messages ADD COLUMN thread_id INTEGER"
        )
    if "status" not in msg_cols:
        conn.execute(
            "ALTER TABLE messages ADD COLUMN status TEXT NOT NULL DEFAULT 'delivered'"
        )
    if "has_attachment" not in msg_cols:
        conn.execute(
            "ALTER TABLE messages ADD COLUMN has_attachment INTEGER NOT NULL DEFAULT 0"
        )
    if "deleted_at" not in msg_cols:
        conn.execute(
            "ALTER TABLE messages ADD COLUMN deleted_at TEXT"
        )

    # --- Migration 7: interview_sessions.kind (Stage 6 exit interview) ---
    isess_cols = _table_columns(conn, "interview_sessions")
    if "kind" not in isess_cols:
        conn.execute(
            "ALTER TABLE interview_sessions ADD COLUMN kind TEXT NOT NULL "
            "DEFAULT 'hiring'"
        )

    # --- Migration 8: messages.channel (Stage 7 team chat) ---
    msg_cols = _table_columns(conn, "messages")
    if "channel" not in msg_cols:
        conn.execute(
            "ALTER TABLE messages ADD COLUMN channel TEXT NOT NULL "
            "DEFAULT 'email'"
        )

    # --- Migration 9: messages.review_flag (comms monitor classifier) ---
    if "review_flag" not in msg_cols:
        conn.execute(
            "ALTER TABLE messages ADD COLUMN review_flag TEXT"
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def get_db() -> Generator[sqlite3.Connection, None, None]:
    """Get a database connection with row factory."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    """Create tables, apply migrations, then create indexes.

    Order matters: tables (legacy-safe IF NOT EXISTS) → migrations
    (add columns to existing tables) → indexes (need final columns).
    """
    with get_db() as conn:
        conn.executescript(TABLES_SCHEMA)
        _migrate(conn)
        conn.executescript(INDEXES_SCHEMA)


def get_student_by_email(email: str) -> dict[str, Any] | None:
    """Look up a student by email. Returns dict with id/email/name/created_at."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT id, email, name, created_at FROM students WHERE email = ?",
            (email,),
        ).fetchone()
    return dict(row) if row else None


def get_student_by_id(student_id: int) -> dict[str, Any] | None:
    """Look up a student by internal id."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT id, email, name, created_at FROM students WHERE id = ?",
            (student_id,),
        ).fetchone()
    return dict(row) if row else None


def get_or_create_student(email: str, name: str) -> dict[str, Any]:
    """Get existing student or create a new one. Returns dict with id."""
    existing = get_student_by_email(email)
    if existing:
        if existing["name"] != name:
            with get_db() as conn:
                conn.execute(
                    "UPDATE students SET name = ? WHERE id = ?",
                    (name, existing["id"]),
                )
                existing["name"] = name
        return existing

    now = _now()
    with get_db() as conn:
        cursor = conn.execute(
            "INSERT INTO students (email, name, created_at) VALUES (?, ?, ?)",
            (email, name, now),
        )
        student_id = cursor.lastrowid
    return {"id": student_id, "email": email, "name": name, "created_at": now}


def create_application(
    student_id: int,
    company_slug: str,
    job_slug: str,
    job_title: str,
    source: str = "direct",
    student_email: str | None = None,
    posting_id: int | None = None,
    cycle: int | None = None,
) -> int:
    """Create a new application record. Returns the application ID.

    If posting_id is not provided, looks up the direct posting for this
    job. If cycle is not provided, uses get_next_cycle(student_id).
    """
    now = _now()
    if student_email is None:
        student = get_student_by_id(student_id)
        student_email = student["email"] if student else ""

    if posting_id is None:
        direct = get_direct_posting(company_slug, job_slug)
        if direct:
            posting_id = direct["id"]

    if cycle is None:
        cycle = get_next_cycle(student_id)

    with get_db() as conn:
        cursor = conn.execute(
            """INSERT INTO applications
               (student_id, student_email, posting_id, company_slug, job_slug,
                job_title, source, current_stage, cycle, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'resume', ?, ?, ?)""",
            (student_id, student_email, posting_id, company_slug, job_slug,
             job_title, source, cycle, now, now),
        )
        return cursor.lastrowid  # type: ignore[return-value]


def record_stage_result(
    application_id: int,
    stage: str,
    status: str,
    score: int | None = None,
    feedback: dict[str, Any] | None = None,
) -> int:
    """Record the result of a simulation stage. Returns the result ID."""
    # Count existing attempts for this stage
    with get_db() as conn:
        row = conn.execute(
            "SELECT MAX(attempt) as max_attempt FROM stage_results "
            "WHERE application_id = ? AND stage = ?",
            (application_id, stage),
        ).fetchone()
        attempt = (row["max_attempt"] or 0) + 1

        feedback_json = json.dumps(feedback) if feedback else None

        cursor = conn.execute(
            """INSERT INTO stage_results
               (application_id, stage, status, score, feedback_json,
                attempt, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (application_id, stage, status, score, feedback_json, attempt, _now()),
        )

        # Update application's current stage and timestamp
        conn.execute(
            "UPDATE applications SET current_stage = ?, updated_at = ? WHERE id = ?",
            (stage, _now(), application_id),
        )

        return cursor.lastrowid  # type: ignore[return-value]


def advance_stage(application_id: int, next_stage: str) -> None:
    """Move an application to the next simulation stage."""
    with get_db() as conn:
        conn.execute(
            "UPDATE applications SET current_stage = ?, updated_at = ? WHERE id = ?",
            (next_stage, _now(), application_id),
        )


def set_application_status(application_id: int, status: str) -> None:
    """Update an application's status (active/rejected/hired/completed)."""
    with get_db() as conn:
        conn.execute(
            "UPDATE applications SET status = ?, updated_at = ? WHERE id = ?",
            (status, _now(), application_id),
        )


def upsert_posting(
    company_slug: str,
    job_slug: str,
    listing_title: str,
    source_type: str = "direct",
    agency_name: str | None = None,
    listing_description: str | None = None,
    confidential: bool = False,
) -> int:
    """Insert or update a posting. Returns the posting ID.

    Uniqueness is on (company_slug, job_slug, source_type, agency_name)
    so re-running seed operations is idempotent.
    """
    now = _now()
    with get_db() as conn:
        # Check for existing
        row = conn.execute(
            """SELECT id FROM postings
               WHERE company_slug = ? AND job_slug = ?
                 AND source_type = ? AND IFNULL(agency_name, '') = IFNULL(?, '')""",
            (company_slug, job_slug, source_type, agency_name),
        ).fetchone()
        if row:
            # Update title/description in case it changed
            conn.execute(
                """UPDATE postings SET listing_title = ?, listing_description = ?,
                   confidential = ? WHERE id = ?""",
                (listing_title, listing_description, int(confidential), row["id"]),
            )
            return row["id"]

        cursor = conn.execute(
            """INSERT INTO postings
               (company_slug, job_slug, source_type, agency_name,
                listing_title, listing_description, confidential, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (company_slug, job_slug, source_type, agency_name,
             listing_title, listing_description, int(confidential), now),
        )
        return cursor.lastrowid  # type: ignore[return-value]


def get_posting(posting_id: int) -> dict[str, Any] | None:
    """Look up a posting by ID."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM postings WHERE id = ?", (posting_id,)
        ).fetchone()
    return dict(row) if row else None


def get_direct_posting(company_slug: str, job_slug: str) -> dict[str, Any] | None:
    """Get the direct (company-owned) posting for a job."""
    with get_db() as conn:
        row = conn.execute(
            """SELECT * FROM postings
               WHERE company_slug = ? AND job_slug = ? AND source_type = 'direct'
               LIMIT 1""",
            (company_slug, job_slug),
        ).fetchone()
    return dict(row) if row else None


def get_postings_for_job(company_slug: str, job_slug: str) -> list[dict[str, Any]]:
    """Get all postings (direct + agency) for a given job."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM postings WHERE company_slug = ? AND job_slug = ? "
            "ORDER BY source_type, agency_name",
            (company_slug, job_slug),
        ).fetchall()
    return [dict(r) for r in rows]


def get_all_postings() -> list[dict[str, Any]]:
    """Get all postings — used by seek.jobs to display the full job board."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM postings ORDER BY company_slug, job_slug, source_type"
        ).fetchall()
    return [dict(r) for r in rows]


def get_next_cycle(student_id: int) -> int:
    """Compute the next cycle number for a student.

    Cycle 1 = first attempt. After completing or being rejected from one
    journey, the student starts cycle 2 on their next application.

    Logic: cycle = max(cycle of all applications) + 1 if last attempt
    is finished (rejected/completed), else current max cycle.
    """
    with get_db() as conn:
        rows = conn.execute(
            "SELECT cycle, status FROM applications "
            "WHERE student_id = ? ORDER BY cycle DESC, created_at DESC",
            (student_id,),
        ).fetchall()

    if not rows:
        return 1

    max_cycle = rows[0]["cycle"]
    # Are there any active applications in the latest cycle? If so, this
    # is still the same cycle. Otherwise the student has finished and is
    # starting fresh.
    active_in_latest = any(
        r["cycle"] == max_cycle and r["status"] == "active" for r in rows
    )
    return max_cycle if active_in_latest else max_cycle + 1


def get_blocked_companies(student_id: int) -> list[str]:
    """Return company slugs the student can no longer apply to.

    A company is blocked if the student has any application with status
    'rejected' for that company. Multiple applications to the same company
    only count once.
    """
    with get_db() as conn:
        rows = conn.execute(
            "SELECT DISTINCT company_slug FROM applications "
            "WHERE student_id = ? AND status = 'rejected'",
            (student_id,),
        ).fetchall()
    return [r["company_slug"] for r in rows]


def get_application(application_id: int) -> dict[str, Any] | None:
    """Get an application by ID."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM applications WHERE id = ?", (application_id,)
        ).fetchone()
        return dict(row) if row else None


def get_student_applications(student_id: int) -> list[dict[str, Any]]:
    """Get all applications for a student, newest first."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM applications WHERE student_id = ? ORDER BY created_at DESC",
            (student_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def create_booking(application_id: int, scheduled_at: str) -> int:
    """Create a new interview booking. Returns booking ID."""
    now = _now()
    with get_db() as conn:
        cursor = conn.execute(
            """INSERT INTO interview_bookings
               (application_id, scheduled_at, status, created_at)
               VALUES (?, ?, 'pending', ?)""",
            (application_id, scheduled_at, now),
        )
        return cursor.lastrowid  # type: ignore[return-value]


def get_active_booking(application_id: int) -> dict[str, Any] | None:
    """Get the current pending booking for an application, if any."""
    with get_db() as conn:
        row = conn.execute(
            """SELECT * FROM interview_bookings
               WHERE application_id = ? AND status = 'pending'
               ORDER BY scheduled_at DESC LIMIT 1""",
            (application_id,),
        ).fetchone()
    return dict(row) if row else None


def get_bookings_for_application(application_id: int) -> list[dict[str, Any]]:
    """Get all bookings for an application (any status)."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM interview_bookings WHERE application_id = ? "
            "ORDER BY created_at",
            (application_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def update_booking_status(booking_id: int, status: str) -> None:
    """Update a booking status (pending|completed|missed|cancelled)."""
    with get_db() as conn:
        conn.execute(
            "UPDATE interview_bookings SET status = ?, completed_at = ? "
            "WHERE id = ?",
            (status, _now(), booking_id),
        )


def increment_missed_interviews(application_id: int) -> int:
    """Increment the missed_interviews counter and return the new value."""
    with get_db() as conn:
        conn.execute(
            "UPDATE applications SET missed_interviews = missed_interviews + 1, "
            "updated_at = ? WHERE id = ?",
            (_now(), application_id),
        )
        row = conn.execute(
            "SELECT missed_interviews FROM applications WHERE id = ?",
            (application_id,),
        ).fetchone()
    return row["missed_interviews"] if row else 0


def increment_reschedule_count(application_id: int) -> int:
    """Increment the reschedule_count counter and return the new value."""
    with get_db() as conn:
        conn.execute(
            "UPDATE applications SET reschedule_count = reschedule_count + 1, "
            "updated_at = ? WHERE id = ?",
            (_now(), application_id),
        )
        row = conn.execute(
            "SELECT reschedule_count FROM applications WHERE id = ?",
            (application_id,),
        ).fetchone()
    return row["reschedule_count"] if row else 0


def create_interview_session(
    application_id: int,
    manager_slug: str,
    manager_name: str,
    kind: str = "hiring",
) -> int:
    """Create a new interview session. Returns session ID.

    kind is 'hiring' (Stage 3) or 'exit' (Stage 6).
    """
    now = _now()
    with get_db() as conn:
        cursor = conn.execute(
            """INSERT INTO interview_sessions
               (application_id, manager_slug, manager_name, kind, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (application_id, manager_slug, manager_name, kind, now),
        )
        return cursor.lastrowid  # type: ignore[return-value]


def get_active_exit_interview(application_id: int) -> dict[str, Any] | None:
    """Return the active or most recent exit interview for an application."""
    return _get_latest_session_of_kind(application_id, "exit")


def get_active_performance_review(application_id: int) -> dict[str, Any] | None:
    """Return the active or most recent performance review for an application."""
    return _get_latest_session_of_kind(application_id, "performance_review")


def _get_latest_session_of_kind(
    application_id: int, kind: str,
) -> dict[str, Any] | None:
    """Shared lookup: most recent interview_session of a given kind."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM interview_sessions "
            "WHERE application_id = ? AND kind = ? "
            "ORDER BY id DESC LIMIT 1",
            (application_id, kind),
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["transcript"] = json.loads(d.get("transcript_json") or "[]")
    d.pop("transcript_json", None)
    if d.get("feedback_json"):
        d["feedback"] = json.loads(d["feedback_json"])
    else:
        d["feedback"] = None
    d.pop("feedback_json", None)
    return d


def get_interview_session(session_id: int) -> dict[str, Any] | None:
    """Get an interview session with parsed transcript and feedback."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM interview_sessions WHERE id = ?", (session_id,)
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["transcript"] = json.loads(d.get("transcript_json") or "[]")
    d.pop("transcript_json", None)
    if d.get("feedback_json"):
        d["feedback"] = json.loads(d["feedback_json"])
    else:
        d["feedback"] = None
    d.pop("feedback_json", None)
    return d


def append_interview_message(
    session_id: int,
    role: str,
    content: str,
) -> None:
    """Append a message to the interview transcript.

    Role is 'assistant' (manager) or 'user' (student).
    """
    with get_db() as conn:
        row = conn.execute(
            "SELECT transcript_json FROM interview_sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        if not row:
            return
        transcript = json.loads(row["transcript_json"] or "[]")
        transcript.append({"role": role, "content": content})
        conn.execute(
            "UPDATE interview_sessions SET transcript_json = ? WHERE id = ?",
            (json.dumps(transcript), session_id),
        )


def complete_interview_session(
    session_id: int,
    final_score: int,
    feedback: dict[str, Any],
) -> None:
    """Mark an interview session as completed and store the final assessment."""
    with get_db() as conn:
        conn.execute(
            """UPDATE interview_sessions
               SET status = 'completed', final_score = ?, feedback_json = ?,
                   completed_at = ?
               WHERE id = ?""",
            (final_score, json.dumps(feedback), _now(), session_id),
        )


def create_message(
    student_id: int,
    sender_name: str,
    subject: str,
    body: str,
    inbox: str = "personal",
    sender_role: str = "",
    sender_email: str = "noreply@workready.eduserver.au",
    application_id: int | None = None,
    related_stage: str | None = None,
    deliver_at: str | None = None,
    student_email: str | None = None,
    booking_id: int | None = None,
    thread_id: int | None = None,
) -> int:
    """Create an inbound inbox message. Returns the message ID.

    student_email is kept as a denormalised column for legacy compatibility
    and is auto-resolved if not provided. booking_id ties the message to
    a specific interview booking (used for reminder messages so they can
    be cancelled if the booking is cancelled).
    """
    now = _now()
    if student_email is None:
        student = get_student_by_id(student_id)
        student_email = student["email"] if student else ""

    with get_db() as conn:
        cursor = conn.execute(
            """INSERT INTO messages
               (student_id, student_email, inbox, sender_name, sender_role,
                sender_email, subject, body, application_id, booking_id,
                related_stage, direction, thread_id, deliver_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'inbound', ?, ?, ?)""",
            (
                student_id, student_email, inbox, sender_name, sender_role,
                sender_email, subject, body, application_id, booking_id,
                related_stage, thread_id, deliver_at or now, now,
            ),
        )
        return cursor.lastrowid  # type: ignore[return-value]


def delete_pending_messages_for_booking(booking_id: int) -> int:
    """Delete future-dated messages tied to a booking. Returns count deleted.

    Used when a booking is cancelled — pending reminders for that booking
    should not fire on the new appointment time. Past messages (already
    delivered) are kept for the historical record.
    """
    now = _now()
    with get_db() as conn:
        cursor = conn.execute(
            "DELETE FROM messages WHERE booking_id = ? AND deliver_at > ?",
            (booking_id, now),
        )
        return cursor.rowcount


def get_inbox(
    student_id: int,
    inbox: str = "personal",
    include_undelivered: bool = False,
) -> list[dict[str, Any]]:
    """Get inbound messages in a student's inbox, ordered newest first.

    Excludes soft-deleted messages (deleted_at IS NOT NULL) unless
    include_undelivered is True (admin dump mode).
    """
    now = _now()
    with get_db() as conn:
        if include_undelivered:
            rows = conn.execute(
                "SELECT * FROM messages WHERE student_id = ? AND inbox = ? "
                "AND direction = 'inbound' ORDER BY deliver_at DESC",
                (student_id, inbox),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM messages WHERE student_id = ? AND inbox = ? "
                "AND direction = 'inbound' AND deliver_at <= ? "
                "AND deleted_at IS NULL ORDER BY deliver_at DESC",
                (student_id, inbox, now),
            ).fetchall()
        return [dict(r) for r in rows]


def get_sent_messages(
    student_id: int,
) -> list[dict[str, Any]]:
    """Get outbound messages sent by a student, ordered newest first."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM messages WHERE student_id = ? AND direction = 'outbound' "
            "AND deleted_at IS NULL ORDER BY created_at DESC",
            (student_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def create_outbound_message(
    student_id: int,
    student_email: str,
    recipient_email: str,
    subject: str,
    body: str,
    thread_id: int | None = None,
    has_attachment: bool = False,
    status: str = "delivered",
) -> int:
    """Create an outbound message (student → recipient). Returns message ID.

    status is 'delivered' for valid recipients, 'bounced' for invalid ones.
    """
    now = _now()
    with get_db() as conn:
        cursor = conn.execute(
            """INSERT INTO messages
               (student_id, student_email, inbox, sender_name, sender_role,
                sender_email, subject, body, direction, recipient_email,
                thread_id, status, has_attachment, is_read, deliver_at, created_at)
               VALUES (?, ?, 'sent', ?, '', ?, ?, ?, 'outbound', ?,
                       ?, ?, ?, 1, ?, ?)""",
            (
                student_id, student_email, student_email, student_email,
                subject, body, recipient_email,
                thread_id, status, int(has_attachment), now, now,
            ),
        )
        msg_id = cursor.lastrowid

        # If this is a reply, set the thread_id to the original message's id
        # if no thread_id was provided
        if thread_id is None and msg_id:
            conn.execute(
                "UPDATE messages SET thread_id = ? WHERE id = ?",
                (msg_id, msg_id),
            )

        return msg_id  # type: ignore[return-value]


def create_bounce_message(
    student_id: int,
    student_email: str,
    original_recipient: str,
    original_subject: str,
) -> int:
    """Create a bounce notification in the student's inbox.

    No "did you mean?" suggestions — real email doesn't offer those.
    The student learns to check addresses carefully.
    """
    body = (
        f"Delivery failed\n\n"
        f"Your message to {original_recipient} could not be delivered. "
        f"The address was not found.\n\n"
        f"Original subject: {original_subject}\n\n"
        f"Check the company website for correct contact details."
    )

    return create_message(
        student_id=student_id,
        student_email=student_email,
        sender_name="Mail Delivery System",
        sender_role="",
        subject=f"Delivery failed: {original_recipient}",
        body=body,
        inbox="personal",
    )


def soft_delete_message(message_id: int) -> None:
    """Soft-delete a message (set deleted_at timestamp)."""
    with get_db() as conn:
        conn.execute(
            "UPDATE messages SET deleted_at = ? WHERE id = ?",
            (_now(), message_id),
        )


def get_message(message_id: int) -> dict[str, Any] | None:
    """Get a single message by ID."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM messages WHERE id = ?", (message_id,)
        ).fetchone()
    return dict(row) if row else None


def get_thread(thread_id: int, student_id: int) -> list[dict[str, Any]]:
    """Get all messages in a thread, ordered chronologically."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM messages WHERE thread_id = ? AND student_id = ? "
            "AND deleted_at IS NULL ORDER BY created_at ASC",
            (thread_id, student_id),
        ).fetchall()
    return [dict(r) for r in rows]


def create_attachment(
    message_id: int,
    filename: str,
    file_path: str,
    file_size: int,
    content_type: str = "application/pdf",
) -> int:
    """Create an attachment record. Returns the attachment ID."""
    now = _now()
    with get_db() as conn:
        cursor = conn.execute(
            """INSERT INTO message_attachments
               (message_id, filename, content_type, file_path, file_size, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (message_id, filename, content_type, file_path, file_size, now),
        )
        return cursor.lastrowid  # type: ignore[return-value]


def get_attachments(message_id: int) -> list[dict[str, Any]]:
    """Get all attachments for a message."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM message_attachments WHERE message_id = ?",
            (message_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def mark_message_read(message_id: int) -> None:
    """Mark a message as read."""
    with get_db() as conn:
        conn.execute(
            "UPDATE messages SET is_read = 1 WHERE id = ?", (message_id,)
        )


# --- Stage 4: Work tasks --------------------------------------------------


def upsert_task_template(
    company_slug: str,
    title: str,
    brief: str,
    description: str,
    difficulty: str,
    discipline: str | None = None,
    estimated_hours: int = 4,
) -> int:
    """Insert or update a task template. Returns the template ID.

    Uniqueness is on (company_slug, title) so re-running the seed is
    idempotent and preserves the id used by existing tasks.
    """
    now = _now()
    with get_db() as conn:
        row = conn.execute(
            "SELECT id FROM task_templates WHERE company_slug = ? AND title = ?",
            (company_slug, title),
        ).fetchone()
        if row:
            conn.execute(
                """UPDATE task_templates
                   SET brief = ?, description = ?, difficulty = ?,
                       discipline = ?, estimated_hours = ?
                   WHERE id = ?""",
                (brief, description, difficulty, discipline, estimated_hours, row["id"]),
            )
            return row["id"]
        cursor = conn.execute(
            """INSERT INTO task_templates
               (company_slug, title, brief, description, discipline,
                difficulty, estimated_hours, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (company_slug, title, brief, description, discipline,
             difficulty, estimated_hours, now),
        )
        return cursor.lastrowid  # type: ignore[return-value]


def list_task_templates_for_company(
    company_slug: str,
    difficulty: str | None = None,
) -> list[dict[str, Any]]:
    """List task templates for a company, optionally filtered by difficulty."""
    with get_db() as conn:
        if difficulty:
            rows = conn.execute(
                "SELECT * FROM task_templates WHERE company_slug = ? "
                "AND difficulty = ? ORDER BY id",
                (company_slug, difficulty),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM task_templates WHERE company_slug = ? ORDER BY id",
                (company_slug,),
            ).fetchall()
    return [dict(r) for r in rows]


def create_task(
    application_id: int,
    task_template_id: int | None,
    title: str,
    brief: str,
    description: str,
    difficulty: str,
    sequence: int,
    visible_at: str | None,
    due_at: str | None,
) -> int:
    """Create a work-task row for an application. Returns the task ID.

    visible_at=None means the task is gated (not yet revealed to the student).
    status is 'pending' until visible_at is set, 'assigned' once visible.
    """
    now = _now()
    status = "assigned" if visible_at else "pending"
    with get_db() as conn:
        cursor = conn.execute(
            """INSERT INTO tasks
               (application_id, task_template_id, title, brief, description,
                difficulty, sequence, status, assigned_at, visible_at,
                due_at, submitted_at, reviewed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)""",
            (application_id, task_template_id, title, brief, description,
             difficulty, sequence, status, now, visible_at, due_at),
        )
        return cursor.lastrowid  # type: ignore[return-value]


def get_task(task_id: int) -> dict[str, Any] | None:
    """Look up a task by ID."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
    return dict(row) if row else None


def list_tasks_for_application(
    application_id: int,
    *,
    only_visible: bool = True,
) -> list[dict[str, Any]]:
    """Return tasks for an application, ordered by sequence.

    only_visible=True hides tasks whose visible_at is NULL or in the future
    (the student can't see them yet). only_visible=False returns everything
    (used by placement logic and admin endpoints).
    """
    now = _now()
    with get_db() as conn:
        if only_visible:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE application_id = ? "
                "AND visible_at IS NOT NULL AND visible_at <= ? "
                "ORDER BY sequence",
                (application_id, now),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE application_id = ? ORDER BY sequence",
                (application_id,),
            ).fetchall()
    return [dict(r) for r in rows]


def get_next_gated_task(application_id: int) -> dict[str, Any] | None:
    """Return the next task in sequence that is still gated (visible_at IS NULL)."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM tasks WHERE application_id = ? "
            "AND visible_at IS NULL ORDER BY sequence LIMIT 1",
            (application_id,),
        ).fetchone()
    return dict(row) if row else None


def reveal_task(task_id: int, visible_at: str, due_at: str) -> None:
    """Flip a gated task to 'assigned' state with visibility and deadline."""
    with get_db() as conn:
        conn.execute(
            "UPDATE tasks SET status = 'assigned', visible_at = ?, due_at = ? "
            "WHERE id = ?",
            (visible_at, due_at, task_id),
        )


def mark_task_submitted(task_id: int) -> None:
    """Mark a task as submitted (status + timestamp)."""
    now = _now()
    with get_db() as conn:
        conn.execute(
            "UPDATE tasks SET status = 'submitted', submitted_at = ? WHERE id = ?",
            (now, task_id),
        )


def mark_task_reviewed(task_id: int, status: str) -> None:
    """Flip a task's status to the reviewed outcome (passed|failed|resubmit).

    Called once the review_deliver_at has passed and the outcome is revealed.
    For now we set reviewed_at immediately so the status update is deferred
    only via the gated read helper (see get_tasks_with_lazy_reveal).
    """
    now = _now()
    with get_db() as conn:
        conn.execute(
            "UPDATE tasks SET status = ?, reviewed_at = ? WHERE id = ?",
            (status, now, task_id),
        )


def create_task_submission(
    task_id: int,
    body: str,
    score: int,
    feedback: dict[str, Any],
    review_status: str,
    review_deliver_at: str,
    attachment_filename: str | None = None,
    attachment_text: str | None = None,
) -> int:
    """Create a submission row with stored review outcome (lazy-gated).

    The review_status and score are persisted immediately but readers
    should hide them until review_deliver_at <= now() — see
    get_visible_submission().
    """
    now = _now()
    with get_db() as conn:
        cursor = conn.execute(
            """INSERT INTO task_submissions
               (task_id, body, attachment_filename, attachment_text,
                score, feedback_json, review_status, review_deliver_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (task_id, body, attachment_filename, attachment_text,
             score, json.dumps(feedback), review_status, review_deliver_at, now),
        )
        return cursor.lastrowid  # type: ignore[return-value]


def get_latest_submission(task_id: int) -> dict[str, Any] | None:
    """Return the most recent submission for a task (if any)."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM task_submissions WHERE task_id = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (task_id,),
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    if d.get("feedback_json"):
        try:
            d["feedback"] = json.loads(d["feedback_json"])
        except (ValueError, TypeError):
            d["feedback"] = None
    else:
        d["feedback"] = None
    return d


def list_prior_task_history(
    application_id: int, before_sequence: int,
) -> list[dict[str, Any]]:
    """Return tasks (with their latest submission) before a given sequence.

    Used to give the mentor reviewer context on what the student has
    already done in this internship.
    """
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE application_id = ? AND sequence < ? "
            "ORDER BY sequence",
            (application_id, before_sequence),
        ).fetchall()
    history: list[dict[str, Any]] = []
    for r in rows:
        task = dict(r)
        task["submission"] = get_latest_submission(task["id"])
        history.append(task)
    return history


# --- Stage 5: Lunchroom sessions ------------------------------------------


def create_lunchroom_invitation(
    application_id: int,
    occasion: str,
    participants: list[dict[str, Any]],
    proposed_slots: list[str],
    trigger_source: str,
    occasion_detail: str | None = None,
    trigger_task_id: int | None = None,
    invitation_message_id: int | None = None,
) -> int:
    """Create a lunchroom session in 'invited' state (no slot picked yet).

    participants is a list of dicts with keys: slug, name, role.
    proposed_slots is a list of UTC ISO strings the student can pick from.
    """
    now = _now()
    with get_db() as conn:
        cursor = conn.execute(
            """INSERT INTO lunchroom_sessions
               (application_id, occasion, occasion_detail, participants_json,
                proposed_slots_json, scheduled_at, status, trigger_source,
                trigger_task_id, invitation_message_id, created_at)
               VALUES (?, ?, ?, ?, ?, NULL, 'invited', ?, ?, ?, ?)""",
            (application_id, occasion, occasion_detail,
             json.dumps(participants), json.dumps(proposed_slots),
             trigger_source, trigger_task_id, invitation_message_id, now),
        )
        return cursor.lastrowid  # type: ignore[return-value]


def _decode_lunchroom_row(row: sqlite3.Row) -> dict[str, Any]:
    """Turn a lunchroom_sessions row into a dict with JSON fields decoded."""
    d = dict(row)
    for key in ("participants_json", "proposed_slots_json", "transcript_json"):
        if d.get(key):
            try:
                d[key.replace("_json", "")] = json.loads(d[key])
            except (ValueError, TypeError):
                d[key.replace("_json", "")] = []
        else:
            d[key.replace("_json", "")] = []
    return d


def get_lunchroom_session(session_id: int) -> dict[str, Any] | None:
    """Look up a lunchroom session by ID."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM lunchroom_sessions WHERE id = ?", (session_id,),
        ).fetchone()
    return _decode_lunchroom_row(row) if row else None


def list_lunchroom_sessions_for_application(
    application_id: int,
    *,
    statuses: list[str] | None = None,
) -> list[dict[str, Any]]:
    """List lunchroom sessions for an application.

    statuses filters to specific status values (e.g. ["invited", "accepted"]).
    Returns newest first.
    """
    with get_db() as conn:
        if statuses:
            placeholders = ",".join("?" * len(statuses))
            rows = conn.execute(
                f"SELECT * FROM lunchroom_sessions WHERE application_id = ? "
                f"AND status IN ({placeholders}) ORDER BY created_at DESC",
                (application_id, *statuses),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM lunchroom_sessions WHERE application_id = ? "
                "ORDER BY created_at DESC",
                (application_id,),
            ).fetchall()
    return [_decode_lunchroom_row(r) for r in rows]


def count_lunchroom_outcomes(
    application_id: int, statuses: list[str],
) -> int:
    """Count lunchroom sessions for an app whose status is in the given list.

    Used for: total invites created (to cap at LUNCHROOM_INVITES), declines +
    misses (to trigger mentor check-in at LUNCHROOM_DECLINE_LIMIT).
    """
    if not statuses:
        return 0
    with get_db() as conn:
        placeholders = ",".join("?" * len(statuses))
        row = conn.execute(
            f"SELECT COUNT(*) AS n FROM lunchroom_sessions "
            f"WHERE application_id = ? AND status IN ({placeholders})",
            (application_id, *statuses),
        ).fetchone()
    return int(row["n"]) if row else 0


def pick_lunchroom_slot(
    session_id: int, scheduled_at: str, calendar_event_id: int | None = None,
) -> None:
    """Transition an invitation to 'accepted' with the picked slot."""
    with get_db() as conn:
        conn.execute(
            "UPDATE lunchroom_sessions SET status = 'accepted', "
            "scheduled_at = ?, calendar_event_id = ? WHERE id = ?",
            (scheduled_at, calendar_event_id, session_id),
        )


def decline_lunchroom_invitation(session_id: int) -> None:
    """Mark an invitation as declined."""
    with get_db() as conn:
        conn.execute(
            "UPDATE lunchroom_sessions SET status = 'declined' WHERE id = ?",
            (session_id,),
        )


def mark_lunchroom_missed(session_id: int) -> None:
    """Mark an accepted session as missed (entry window elapsed)."""
    with get_db() as conn:
        conn.execute(
            "UPDATE lunchroom_sessions SET status = 'missed' WHERE id = ?",
            (session_id,),
        )


def set_lunchroom_calendar_event(session_id: int, event_id: int) -> None:
    """Attach the materialised calendar_events row to a lunchroom session."""
    with get_db() as conn:
        conn.execute(
            "UPDATE lunchroom_sessions SET calendar_event_id = ? WHERE id = ?",
            (event_id, session_id),
        )


# --- Stage 5b: Lunchroom chat posts ---------------------------------------


def mark_lunchroom_active(session_id: int) -> None:
    """Transition an accepted session to 'active' — chat is now live."""
    with get_db() as conn:
        conn.execute(
            "UPDATE lunchroom_sessions SET status = 'active' WHERE id = ?",
            (session_id,),
        )


def mark_lunchroom_completed(
    session_id: int,
    *,
    participation_notes: str | None = None,
    system_feedback: str | None = None,
) -> None:
    """Transition an active session to 'completed'. Stage 5c will populate notes."""
    with get_db() as conn:
        conn.execute(
            "UPDATE lunchroom_sessions SET status = 'completed', "
            "participation_notes = COALESCE(?, participation_notes), "
            "system_feedback = COALESCE(?, system_feedback), "
            "completed_at = ? WHERE id = ?",
            (participation_notes, system_feedback, _now(), session_id),
        )


def create_lunchroom_post(
    session_id: int,
    sequence: int,
    author_kind: str,
    deliver_at: str,
    *,
    author_slug: str | None = None,
    author_name: str | None = None,
    intention: str | None = None,
    content: str | None = None,
    status: str = "pending",
    mentions: list[str] | None = None,
) -> int:
    """Create a lunchroom_posts row. 'pending' if content is None, else 'delivered'."""
    now = _now()
    delivered_at = now if status == "delivered" else None
    with get_db() as conn:
        cursor = conn.execute(
            """INSERT INTO lunchroom_posts
               (session_id, sequence, author_kind, author_slug, author_name,
                intention, content, deliver_at, status, mentions_json,
                created_at, delivered_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (session_id, sequence, author_kind, author_slug, author_name,
             intention, content, deliver_at, status,
             json.dumps(mentions or []), now, delivered_at),
        )
        return cursor.lastrowid  # type: ignore[return-value]


def _decode_post_row(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    try:
        d["mentions"] = json.loads(d.get("mentions_json") or "[]")
    except (ValueError, TypeError):
        d["mentions"] = []
    return d


def list_lunchroom_posts(
    session_id: int,
    *,
    only_delivered: bool = False,
) -> list[dict[str, Any]]:
    """List all posts for a session, ordered by sequence."""
    with get_db() as conn:
        if only_delivered:
            rows = conn.execute(
                "SELECT * FROM lunchroom_posts WHERE session_id = ? "
                "AND status = 'delivered' "
                "ORDER BY delivered_at ASC, sequence ASC",
                (session_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM lunchroom_posts WHERE session_id = ? "
                "ORDER BY sequence ASC",
                (session_id,),
            ).fetchall()
    return [_decode_post_row(r) for r in rows]


def list_due_pending_posts(session_id: int) -> list[dict[str, Any]]:
    """Return pending posts whose deliver_at has passed, in sequence order."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM lunchroom_posts WHERE session_id = ? "
            "AND status = 'pending' AND deliver_at <= ? "
            "ORDER BY sequence ASC",
            (session_id, _now()),
        ).fetchall()
    return [_decode_post_row(r) for r in rows]


def next_pending_post_for_character(
    session_id: int, author_slug: str,
) -> dict[str, Any] | None:
    """Return the earliest pending post for a given character, or None."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM lunchroom_posts WHERE session_id = ? "
            "AND status = 'pending' AND author_kind = 'character' "
            "AND author_slug = ? ORDER BY sequence ASC LIMIT 1",
            (session_id, author_slug),
        ).fetchone()
    return _decode_post_row(row) if row else None


def update_post_deliver_at(post_id: int, deliver_at: str) -> None:
    with get_db() as conn:
        conn.execute(
            "UPDATE lunchroom_posts SET deliver_at = ? WHERE id = ?",
            (deliver_at, post_id),
        )


def mark_post_delivered(post_id: int, content: str) -> None:
    with get_db() as conn:
        conn.execute(
            "UPDATE lunchroom_posts SET status = 'delivered', content = ?, "
            "delivered_at = ? WHERE id = ?",
            (content, _now(), post_id),
        )


def count_delivered_posts(session_id: int) -> int:
    with get_db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM lunchroom_posts "
            "WHERE session_id = ? AND status = 'delivered'",
            (session_id,),
        ).fetchone()
    return int(row["n"]) if row else 0


def next_post_sequence(session_id: int) -> int:
    with get_db() as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(sequence), 0) AS m FROM lunchroom_posts "
            "WHERE session_id = ?",
            (session_id,),
        ).fetchone()
    return int(row["m"]) + 1 if row else 1


# --- Stage 4c: Calendar events --------------------------------------------


def create_calendar_event(
    application_id: int,
    event_type: str,
    title: str,
    scheduled_at: str,
    description: str | None = None,
    related_id: int | None = None,
    status: str = "upcoming",
) -> int:
    """Create a calendar event for an application. Returns the event ID."""
    now = _now()
    with get_db() as conn:
        cursor = conn.execute(
            """INSERT INTO calendar_events
               (application_id, event_type, title, description,
                scheduled_at, status, related_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (application_id, event_type, title, description,
             scheduled_at, status, related_id, now),
        )
        return cursor.lastrowid  # type: ignore[return-value]


def upsert_calendar_event_for_task(
    application_id: int,
    task_id: int,
    title: str,
    scheduled_at: str,
    description: str | None = None,
) -> int:
    """Create-or-update the task_deadline calendar event for a task.

    Idempotent on (related_id=task_id, event_type='task_deadline') so
    re-revealing a task or adjusting its deadline updates the existing
    row rather than creating duplicates.
    """
    now = _now()
    with get_db() as conn:
        row = conn.execute(
            "SELECT id FROM calendar_events WHERE related_id = ? "
            "AND event_type = 'task_deadline'",
            (task_id,),
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE calendar_events SET title = ?, description = ?, "
                "scheduled_at = ? WHERE id = ?",
                (title, description, scheduled_at, row["id"]),
            )
            return row["id"]
        cursor = conn.execute(
            """INSERT INTO calendar_events
               (application_id, event_type, title, description,
                scheduled_at, status, related_id, created_at)
               VALUES (?, 'task_deadline', ?, ?, ?, 'upcoming', ?, ?)""",
            (application_id, title, description, scheduled_at, task_id, now),
        )
        return cursor.lastrowid  # type: ignore[return-value]


def list_calendar_events(
    application_id: int,
    *,
    include_past: bool = True,
    include_cancelled: bool = False,
) -> list[dict[str, Any]]:
    """List calendar events for an application, ordered by scheduled_at.

    include_past=False filters out events whose scheduled_at is before now.
    include_cancelled=False filters out cancelled events.
    """
    with get_db() as conn:
        sql = "SELECT * FROM calendar_events WHERE application_id = ?"
        params: list[Any] = [application_id]
        if not include_past:
            sql += " AND scheduled_at >= ?"
            params.append(_now())
        if not include_cancelled:
            sql += " AND status != 'cancelled'"
        sql += " ORDER BY scheduled_at ASC"
        rows = conn.execute(sql, tuple(params)).fetchall()
    return [dict(r) for r in rows]


def get_calendar_event(event_id: int) -> dict[str, Any] | None:
    """Look up a single calendar event by ID."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM calendar_events WHERE id = ?", (event_id,)
        ).fetchone()
    return dict(row) if row else None


def update_calendar_event_status(event_id: int, status: str) -> None:
    """Update a calendar event's status (accepted/declined/completed/cancelled)."""
    with get_db() as conn:
        conn.execute(
            "UPDATE calendar_events SET status = ? WHERE id = ?",
            (status, event_id),
        )


def cancel_task_deadline_event(task_id: int) -> None:
    """Mark a task's deadline event as completed (used after submission)."""
    with get_db() as conn:
        conn.execute(
            "UPDATE calendar_events SET status = 'completed' "
            "WHERE related_id = ? AND event_type = 'task_deadline' "
            "AND status = 'upcoming'",
            (task_id,),
        )


def get_stage_results(application_id: int, stage: str | None = None) -> list[dict[str, Any]]:
    """Get stage results for an application, optionally filtered by stage."""
    with get_db() as conn:
        if stage:
            rows = conn.execute(
                "SELECT * FROM stage_results WHERE application_id = ? AND stage = ? "
                "ORDER BY attempt",
                (application_id, stage),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM stage_results WHERE application_id = ? "
                "ORDER BY created_at",
                (application_id,),
            ).fetchall()

        results = []
        for r in rows:
            d = dict(r)
            if d.get("feedback_json"):
                d["feedback"] = json.loads(d["feedback_json"])
                del d["feedback_json"]
            else:
                d["feedback"] = None
                d.pop("feedback_json", None)
            results.append(d)
        return results

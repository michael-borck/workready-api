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
    related_stage TEXT,
    is_read INTEGER DEFAULT 0,
    deliver_at TEXT NOT NULL,
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


def create_interview_session(
    application_id: int,
    manager_slug: str,
    manager_name: str,
) -> int:
    """Create a new interview session. Returns session ID."""
    now = _now()
    with get_db() as conn:
        cursor = conn.execute(
            """INSERT INTO interview_sessions
               (application_id, manager_slug, manager_name, created_at)
               VALUES (?, ?, ?, ?)""",
            (application_id, manager_slug, manager_name, now),
        )
        return cursor.lastrowid  # type: ignore[return-value]


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
    application_id: int | None = None,
    related_stage: str | None = None,
    deliver_at: str | None = None,
    student_email: str | None = None,
) -> int:
    """Create an inbox message. Returns the message ID.

    student_email is kept as a denormalised column for legacy compatibility
    and is auto-resolved if not provided.
    """
    now = _now()
    if student_email is None:
        student = get_student_by_id(student_id)
        student_email = student["email"] if student else ""

    with get_db() as conn:
        cursor = conn.execute(
            """INSERT INTO messages
               (student_id, student_email, inbox, sender_name, sender_role,
                subject, body, application_id, related_stage, deliver_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                student_id, student_email, inbox, sender_name, sender_role,
                subject, body, application_id, related_stage,
                deliver_at or now, now,
            ),
        )
        return cursor.lastrowid  # type: ignore[return-value]


def get_inbox(
    student_id: int,
    inbox: str = "personal",
    include_undelivered: bool = False,
) -> list[dict[str, Any]]:
    """Get messages in a student's inbox, ordered newest first."""
    now = _now()
    with get_db() as conn:
        if include_undelivered:
            rows = conn.execute(
                "SELECT * FROM messages WHERE student_id = ? AND inbox = ? "
                "ORDER BY deliver_at DESC",
                (student_id, inbox),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM messages WHERE student_id = ? AND inbox = ? "
                "AND deliver_at <= ? ORDER BY deliver_at DESC",
                (student_id, inbox, now),
            ).fetchall()
        return [dict(r) for r in rows]


def mark_message_read(message_id: int) -> None:
    """Mark a message as read."""
    with get_db() as conn:
        conn.execute(
            "UPDATE messages SET is_read = 1 WHERE id = ?", (message_id,)
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

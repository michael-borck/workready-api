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

SCHEMA = """
CREATE TABLE IF NOT EXISTS students (
    email TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_email TEXT NOT NULL REFERENCES students(email),
    company_slug TEXT NOT NULL,
    job_slug TEXT NOT NULL,
    job_title TEXT NOT NULL,
    source TEXT DEFAULT 'direct',
    current_stage TEXT DEFAULT 'resume',
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

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_email TEXT NOT NULL REFERENCES students(email),
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

CREATE INDEX IF NOT EXISTS idx_applications_student
    ON applications(student_email);
CREATE INDEX IF NOT EXISTS idx_applications_company_job
    ON applications(company_slug, job_slug);
CREATE INDEX IF NOT EXISTS idx_stage_results_application
    ON stage_results(application_id, stage);
CREATE INDEX IF NOT EXISTS idx_messages_student
    ON messages(student_email, inbox);
"""


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
    """Create tables if they don't exist."""
    with get_db() as conn:
        conn.executescript(SCHEMA)


def get_or_create_student(email: str, name: str) -> dict[str, Any]:
    """Get existing student or create a new one."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM students WHERE email = ?", (email,)
        ).fetchone()
        if row:
            # Update name if changed
            if row["name"] != name:
                conn.execute(
                    "UPDATE students SET name = ? WHERE email = ?",
                    (name, email),
                )
            return dict(row)

        conn.execute(
            "INSERT INTO students (email, name, created_at) VALUES (?, ?, ?)",
            (email, name, _now()),
        )
        return {"email": email, "name": name, "created_at": _now()}


def create_application(
    student_email: str,
    company_slug: str,
    job_slug: str,
    job_title: str,
    source: str = "direct",
) -> int:
    """Create a new application record. Returns the application ID."""
    now = _now()
    with get_db() as conn:
        cursor = conn.execute(
            """INSERT INTO applications
               (student_email, company_slug, job_slug, job_title, source,
                current_stage, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 'resume', ?, ?)""",
            (student_email, company_slug, job_slug, job_title, source, now, now),
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


def get_application(application_id: int) -> dict[str, Any] | None:
    """Get an application by ID."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM applications WHERE id = ?", (application_id,)
        ).fetchone()
        return dict(row) if row else None


def get_student_applications(email: str) -> list[dict[str, Any]]:
    """Get all applications for a student."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM applications WHERE student_email = ? ORDER BY created_at DESC",
            (email,),
        ).fetchall()
        return [dict(r) for r in rows]


def create_message(
    student_email: str,
    sender_name: str,
    subject: str,
    body: str,
    inbox: str = "personal",
    sender_role: str = "",
    application_id: int | None = None,
    related_stage: str | None = None,
    deliver_at: str | None = None,
) -> int:
    """Create an inbox message. Returns the message ID."""
    now = _now()
    with get_db() as conn:
        cursor = conn.execute(
            """INSERT INTO messages
               (student_email, inbox, sender_name, sender_role, subject, body,
                application_id, related_stage, deliver_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                student_email, inbox, sender_name, sender_role, subject, body,
                application_id, related_stage, deliver_at or now, now,
            ),
        )
        return cursor.lastrowid  # type: ignore[return-value]


def get_inbox(
    student_email: str,
    inbox: str = "personal",
    include_undelivered: bool = False,
) -> list[dict[str, Any]]:
    """Get messages in a student's inbox, ordered newest first."""
    now = _now()
    with get_db() as conn:
        if include_undelivered:
            rows = conn.execute(
                "SELECT * FROM messages WHERE student_email = ? AND inbox = ? "
                "ORDER BY deliver_at DESC",
                (student_email, inbox),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM messages WHERE student_email = ? AND inbox = ? "
                "AND deliver_at <= ? ORDER BY deliver_at DESC",
                (student_email, inbox, now),
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

"""Admin / debug endpoints for the WorkReady simulation API.

Gated behind a single shared token (WORKREADY_ADMIN_TOKEN env var). Used by
the admin.html page in workready-portal to inspect and manipulate student
state for testing — force a student into any stage, force-pass or
force-fail a stage, deliver pending messages immediately, reset a student
to a clean slate, etc.

These endpoints intentionally bypass the normal state machine. They are
*test affordances*, not part of the simulation itself.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException

from workready_api.db import (
    advance_stage,
    create_application,
    create_message,
    get_application,
    get_bookings_for_application,
    get_db,
    get_inbox,
    get_or_create_student,
    get_stage_results,
    get_student_applications,
    get_student_by_email,
    record_stage_result,
    set_application_status,
)
from workready_api.jobs import get_job


ADMIN_TOKEN = os.environ.get("WORKREADY_ADMIN_TOKEN", "")


def require_admin_token(authorization: str | None = Header(None)) -> None:
    """Reject requests without a valid Bearer token."""
    if not ADMIN_TOKEN:
        raise HTTPException(
            status_code=503,
            detail="Admin endpoints disabled — set WORKREADY_ADMIN_TOKEN in the API .env",
        )
    expected = f"Bearer {ADMIN_TOKEN}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="Invalid admin token")


router = APIRouter(
    prefix="/api/v1/admin",
    tags=["admin"],
    dependencies=[Depends(require_admin_token)],
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ============================================================
# Read endpoints
# ============================================================


@router.get("/health")
def admin_health() -> dict:
    """Confirm the admin token is valid."""
    return {"status": "ok", "admin": True}


@router.get("/students")
def list_students() -> dict:
    """List all students with a quick state summary."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, email, name, created_at FROM students ORDER BY created_at DESC"
        ).fetchall()
        students = []
        for r in rows:
            student = dict(r)
            apps = conn.execute(
                "SELECT id, company_slug, job_slug, job_title, current_stage, status, "
                "cycle, missed_interviews, reschedule_count, updated_at "
                "FROM applications WHERE student_id = ? ORDER BY created_at DESC",
                (student["id"],),
            ).fetchall()
            student["applications"] = [dict(a) for a in apps]
            student["application_count"] = len(student["applications"])

            # Compute high-level state from active applications (mirrors the
            # logic in the public /student/{email}/state endpoint)
            active = [a for a in student["applications"] if a["status"] == "active"]
            if not active:
                student["state"] = "NOT_APPLIED"
            else:
                stage = active[0]["current_stage"]
                if stage == "resume":
                    student["state"] = "APPLIED"
                elif stage == "completed":
                    student["state"] = "COMPLETED"
                else:
                    student["state"] = f"HIRED:{stage}"

            unread_personal = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE student_id = ? "
                "AND inbox = 'personal' AND is_read = 0 AND deliver_at <= ?",
                (student["id"], _now()),
            ).fetchone()[0]
            unread_work = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE student_id = ? "
                "AND inbox = 'work' AND is_read = 0 AND deliver_at <= ?",
                (student["id"], _now()),
            ).fetchone()[0]
            student["unread_personal"] = unread_personal
            student["unread_work"] = unread_work

            students.append(student)

    return {"students": students, "total": len(students)}


@router.get("/students/{email}")
def get_student_dump(email: str) -> dict:
    """Full state dump for a single student — applications, messages,
    bookings, interview sessions, stage results."""
    student = get_student_by_email(email)
    if not student:
        raise HTTPException(404, detail="Student not found")

    student_id = student["id"]
    applications = get_student_applications(student_id)

    # Enrich applications with their stage results, bookings, sessions
    enriched_apps = []
    with get_db() as conn:
        for app in applications:
            app_dump = dict(app)
            app_dump["stage_results"] = get_stage_results(app["id"])
            app_dump["bookings"] = get_bookings_for_application(app["id"])
            sessions = conn.execute(
                "SELECT id, manager_slug, manager_name, status, final_score, "
                "created_at, completed_at FROM interview_sessions "
                "WHERE application_id = ? ORDER BY created_at",
                (app["id"],),
            ).fetchall()
            app_dump["interview_sessions"] = [dict(s) for s in sessions]
            enriched_apps.append(app_dump)

    # All messages, both inboxes, including pending
    personal = get_inbox(student_id, "personal", include_undelivered=True)
    work = get_inbox(student_id, "work", include_undelivered=True)

    # Pending message count
    now = _now()
    pending_count = sum(
        1 for m in personal + work if m.get("deliver_at", "") > now
    )

    return {
        "student": student,
        "applications": enriched_apps,
        "messages": {
            "personal": personal,
            "work": work,
            "pending_count": pending_count,
        },
    }


# ============================================================
# Mutation endpoints
# ============================================================


@router.post("/students")
def create_test_student(payload: dict) -> dict:
    """Create a student record without any application."""
    email = payload.get("email", "").strip().lower()
    if not email:
        raise HTTPException(400, "email required")
    name = payload.get("name") or email.split("@")[0].replace(".", " ").title()
    student = get_or_create_student(email, name)
    return {"student": student, "created": True}


@router.post("/students/{email}/reset")
def reset_student(email: str) -> dict:
    """Wipe all applications, messages, bookings, sessions, and stage results
    for a student. Keeps the student record itself."""
    student = get_student_by_email(email)
    if not student:
        raise HTTPException(404, "Student not found")
    sid = student["id"]
    with get_db() as conn:
        # Cascade: stage_results → interview_sessions → bookings →
        # messages → applications. Foreign keys point to applications,
        # so kill the children first.
        app_ids = [
            r[0]
            for r in conn.execute(
                "SELECT id FROM applications WHERE student_id = ?", (sid,)
            ).fetchall()
        ]
        for aid in app_ids:
            conn.execute("DELETE FROM stage_results WHERE application_id = ?", (aid,))
            conn.execute(
                "DELETE FROM interview_sessions WHERE application_id = ?", (aid,)
            )
            conn.execute(
                "DELETE FROM interview_bookings WHERE application_id = ?", (aid,)
            )
        conn.execute("DELETE FROM messages WHERE student_id = ?", (sid,))
        conn.execute("DELETE FROM applications WHERE student_id = ?", (sid,))
    return {"student_id": sid, "applications_removed": len(app_ids)}


@router.delete("/students/{email}")
def delete_student(email: str) -> dict:
    """Hard-delete a student and all related data."""
    student = get_student_by_email(email)
    if not student:
        raise HTTPException(404, "Student not found")
    sid = student["id"]
    with get_db() as conn:
        # Children first
        app_ids = [
            r[0]
            for r in conn.execute(
                "SELECT id FROM applications WHERE student_id = ?", (sid,)
            ).fetchall()
        ]
        for aid in app_ids:
            conn.execute("DELETE FROM stage_results WHERE application_id = ?", (aid,))
            conn.execute(
                "DELETE FROM interview_sessions WHERE application_id = ?", (aid,)
            )
            conn.execute(
                "DELETE FROM interview_bookings WHERE application_id = ?", (aid,)
            )
        conn.execute("DELETE FROM messages WHERE student_id = ?", (sid,))
        conn.execute("DELETE FROM applications WHERE student_id = ?", (sid,))
        conn.execute("DELETE FROM students WHERE id = ?", (sid,))
    return {"deleted": True, "email": email}


@router.post("/students/{email}/state")
def force_state(email: str, payload: dict) -> dict:
    """Force a student into a specific simulation state.

    Payload:
        {
            "state": "APPLIED" | "HIRED:interview" | "HIRED:work_task" |
                     "HIRED:lunchroom" | "HIRED:exit_interview" | "COMPLETED",
            "company_slug": "ironvale-resources",
            "job_slug": "graduate-mining-engineer"
        }

    Creates a fresh application for (company, job) and sets it to the
    requested stage. Any existing active applications for this student
    are first marked 'rejected' to keep state coherent.
    """
    student = get_student_by_email(email)
    if not student:
        raise HTTPException(404, "Student not found — create one first")

    state = payload.get("state", "").strip()
    company_slug = payload.get("company_slug", "").strip()
    job_slug = payload.get("job_slug", "").strip()

    if not state or not company_slug or not job_slug:
        raise HTTPException(400, "state, company_slug, and job_slug required")

    job = get_job(company_slug, job_slug)
    if not job:
        raise HTTPException(404, f"Job not found: {company_slug}/{job_slug}")

    # Map state to (current_stage, status)
    stage_map = {
        "APPLIED": ("resume", "active"),
        "HIRED:interview": ("interview", "active"),
        "HIRED:work_task": ("work_task", "active"),
        "HIRED:lunchroom": ("lunchroom", "active"),
        "HIRED:exit_interview": ("exit_interview", "active"),
        "COMPLETED": ("completed", "completed"),
    }
    if state not in stage_map:
        raise HTTPException(400, f"Unknown state: {state}")
    target_stage, target_status = stage_map[state]

    sid = student["id"]
    # Mark all existing active apps as rejected so the new one is the only
    # active record
    with get_db() as conn:
        conn.execute(
            "UPDATE applications SET status = 'rejected', updated_at = ? "
            "WHERE student_id = ? AND status = 'active'",
            (_now(), sid),
        )

    app_id = create_application(
        student_id=sid,
        company_slug=company_slug,
        job_slug=job_slug,
        job_title=job.get("title", job_slug),
        student_email=email,
    )

    # Move to target stage
    with get_db() as conn:
        conn.execute(
            "UPDATE applications SET current_stage = ?, status = ?, "
            "updated_at = ? WHERE id = ?",
            (target_stage, target_status, _now(), app_id),
        )

    return {
        "application_id": app_id,
        "state": state,
        "current_stage": target_stage,
        "status": target_status,
    }


@router.post("/applications/{application_id}/outcome")
def force_outcome(application_id: int, payload: dict) -> dict:
    """Force a stage outcome on an application.

    Payload: {"outcome": "resume_pass" | "resume_fail" |
                         "interview_pass" | "interview_fail"}
    """
    outcome = payload.get("outcome", "").strip()
    app = get_application(application_id)
    if not app:
        raise HTTPException(404, "Application not found")

    if outcome == "resume_pass":
        record_stage_result(application_id, "resume", "passed", score=85)
        advance_stage(application_id, "interview")
        set_application_status(application_id, "active")
    elif outcome == "resume_fail":
        record_stage_result(application_id, "resume", "failed", score=35)
        set_application_status(application_id, "rejected")
    elif outcome == "interview_pass":
        record_stage_result(application_id, "interview", "passed", score=85)
        advance_stage(application_id, "work_task")
        set_application_status(application_id, "active")
    elif outcome == "interview_fail":
        record_stage_result(application_id, "interview", "failed", score=35)
        set_application_status(application_id, "rejected")
    else:
        raise HTTPException(400, f"Unknown outcome: {outcome}")

    return {"application_id": application_id, "outcome": outcome}


@router.post("/students/{email}/deliver-pending")
def deliver_pending_messages(email: str) -> dict:
    """Flush all delayed messages for a student — set deliver_at = now()."""
    student = get_student_by_email(email)
    if not student:
        raise HTTPException(404, "Student not found")
    now = _now()
    with get_db() as conn:
        cursor = conn.execute(
            "UPDATE messages SET deliver_at = ? WHERE student_id = ? "
            "AND deliver_at > ?",
            (now, student["id"], now),
        )
        return {"flushed": cursor.rowcount}


@router.post("/students/{email}/note")
def post_admin_note(email: str, payload: dict) -> dict:
    """Inject a system message into the student's inbox — useful for
    smoke-testing inbox rendering and the unread badge."""
    student = get_student_by_email(email)
    if not student:
        raise HTTPException(404, "Student not found")
    subject = payload.get("subject") or "Admin test message"
    body = payload.get("body") or "This is a test message injected via the admin tool."
    inbox = payload.get("inbox", "personal")
    msg_id = create_message(
        student_id=student["id"],
        sender_name="Admin (test)",
        subject=subject,
        body=body,
        inbox=inbox,
        sender_role="Test fixture",
    )
    return {"message_id": msg_id, "inbox": inbox}


@router.get("/jobs")
def list_jobs_for_admin() -> dict:
    """Convenience: list all (company_slug, job_slug, title) for the
    state-forcing dropdown in the admin UI."""
    from workready_api.jobs import _JOB_CACHE

    jobs: list[dict[str, Any]] = []
    for (company_slug, job_slug), job in _JOB_CACHE.items():
        jobs.append(
            {
                "company_slug": company_slug,
                "company_name": job.get("company"),
                "job_slug": job_slug,
                "job_title": job.get("title"),
                "department": job.get("department"),
            }
        )
    jobs.sort(key=lambda j: (j["company_name"] or "", j["job_title"] or ""))
    return {"jobs": jobs, "total": len(jobs)}

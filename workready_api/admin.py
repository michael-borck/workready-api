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
import secrets
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException

from workready_api.db import (
    advance_stage,
    create_application,
    create_message,
    generate_codes,
    get_application,
    get_bookings_for_application,
    get_code,
    get_db,
    get_inbox,
    get_stage_results,
    get_student_applications,
    get_student_by_code,
    list_codes,
    record_stage_result,
    revoke_code,
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
    if not authorization or not secrets.compare_digest(authorization, expected):
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
            "SELECT id, code, handle, display_name, created_at, last_login_at "
            "FROM students ORDER BY created_at DESC"
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

            # Compute high-level state from live applications (mirrors the
            # logic in the public /student/{code}/state endpoint). 'hired'
            # and 'completed' applications drive state just like 'active'.
            live = [
                a for a in student["applications"]
                if a["status"] in ("active", "hired", "completed")
            ]
            if not live:
                student["state"] = "NOT_APPLIED"
            else:
                stage = live[0]["current_stage"]
                if stage == "resume":
                    student["state"] = "APPLIED"
                elif stage == "completed" or live[0]["status"] == "completed":
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


@router.get("/funnel")
def cohort_funnel() -> dict:
    """Cohort funnel — where every student sits in the internship arc.

    One row per application bucketed by live stage, plus aggregate counters.
    Built for the lecturer's cohort view: spot who's stuck where at a glance.
    """
    stages = ["resume", "interview", "placement", "mid_placement", "exit"]
    funnel = [{"key": s, "label": stage_label(s), "count": 0} for s in stages]
    completed = rejected = hired = 0
    with get_db() as conn:
        students_total = conn.execute("SELECT COUNT(*) FROM students").fetchone()[0]
        codes_total = conn.execute("SELECT COUNT(*) FROM codes").fetchone()[0]
        codes_redeemed = conn.execute(
            "SELECT COUNT(*) FROM codes WHERE redeemed_at IS NOT NULL"
        ).fetchone()[0]
        rows = conn.execute(
            "SELECT current_stage, status, COUNT(*) AS n FROM applications "
            "GROUP BY current_stage, status"
        ).fetchall()
        for r in rows:
            stage, status, n = r["current_stage"], r["status"], r["n"]
            if status == "rejected":
                rejected += n
            elif status == "completed" or stage == "completed":
                completed += n
            elif status == "hired" or stage in stages:
                hired += n
                for f in funnel:
                    if f["key"] == stage:
                        f["count"] += n
    return {
        "students": students_total,
        "codes": codes_total,
        "codes_redeemed": codes_redeemed,
        "funnel": funnel,
        "hired": hired,
        "completed": completed,
        "rejected": rejected,
    }


def stage_label(stage: str) -> str:
    return {
        "resume": "Applied (under review)",
        "interview": "Interview stage",
        "placement": "On placement",
        "mid_placement": "Mid-placement",
        "exit": "Exit interview",
    }.get(stage, stage.replace("_", " ").title())


@router.get("/applications/{application_id}/journey-report")
def get_journey_report(application_id: int) -> dict:
    """Lecturer-friendly journey report for a single application.

    Per-stage breakdown with scores and qualitative feedback. Designed
    for grading workflows — the lecturer reads this end-to-end and
    makes the final mark themselves. No aggregate score, no AI
    judgement layer on top of the existing assessor outputs.
    """
    from workready_api.journey_report import build_journey_report
    report = build_journey_report(application_id)
    if not report:
        raise HTTPException(404, detail="Application not found")
    return report


@router.get("/students/{code}")
def get_student_dump(code: str) -> dict:
    """Full state dump for a single student — applications, messages,
    bookings, interview sessions, stage results."""
    student = get_student_by_code(code)
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
# Access-code management
# ============================================================


@router.post("/codes/generate")
def generate_access_codes(payload: dict) -> dict:
    """Issue new access codes. Returns the codes — and nothing else.

    The code→person mapping never enters this system: the lecturer pairs
    the returned codes with students in their own LMS/spreadsheet.

    Payload: {"count": 10, "cohort": "sem1-2026", "note": "optional memo"}
    """
    try:
        count = int(payload.get("count", 1))
    except (TypeError, ValueError):
        raise HTTPException(400, "count must be an integer")
    cohort = (payload.get("cohort") or "default").strip() or "default"
    note = payload.get("note")
    if count < 1 or count > 1000:
        raise HTTPException(400, "count must be between 1 and 1000")
    codes = generate_codes(count, cohort=cohort, note=note)
    return {"codes": codes, "cohort": cohort, "total": len(codes)}


@router.get("/codes")
def list_access_codes(
    cohort: str | None = None,
    include_inactive: bool = True,
) -> dict:
    """List issued access codes (no identities are stored or returned)."""
    codes = list_codes(cohort=cohort, include_inactive=include_inactive)
    return {"codes": codes, "total": len(codes)}


@router.get("/codes/{code}")
def get_access_code(code: str) -> dict:
    """Inspect a single code — active state, redemption, cohort, note."""
    row = get_code(code)
    if not row:
        raise HTTPException(404, detail="Code not found")
    return row


@router.post("/codes/{code}/revoke")
def revoke_access_code(code: str) -> dict:
    """Deactivate a code. Its student can no longer sign in."""
    if not revoke_code(code):
        raise HTTPException(404, detail="Active code not found")
    return {"code": code.upper(), "active": False}


# ============================================================
# Mutation endpoints
# ============================================================


@router.post("/students/{code}/reset")
def reset_student(code: str) -> dict:
    """Wipe all applications, messages, bookings, sessions, and stage results
    for a student. Keeps the student record itself."""
    student = get_student_by_code(code)
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


@router.delete("/students/{code}")
def delete_student(code: str) -> dict:
    """Hard-delete a student and all related data. The access code stays
    active — revoke it separately if it should not be reusable."""
    student = get_student_by_code(code)
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
    return {"deleted": True, "code": student["code"]}


@router.post("/students/{code}/state")
def force_state(code: str, payload: dict) -> dict:
    """Force a student into a specific simulation state.

    Payload:
        {
            "state": "APPLIED" | "HIRED:interview" | "HIRED:placement" |
                     "HIRED:mid_placement" | "HIRED:exit" | "COMPLETED",
            "company_slug": "ironvale-resources",
            "job_slug": "graduate-mining-engineer"
        }

    Creates a fresh application for (company, job) and sets it to the
    requested stage. Any existing active applications for this student
    are first marked 'rejected' to keep state coherent.
    """
    student = get_student_by_code(code)
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
        "HIRED:placement": ("placement", "active"),
        "HIRED:mid_placement": ("mid_placement", "active"),
        "HIRED:exit": ("exit", "active"),
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
        advance_stage(application_id, "placement")
        set_application_status(application_id, "active")
    elif outcome == "interview_fail":
        record_stage_result(application_id, "interview", "failed", score=35)
        set_application_status(application_id, "rejected")
    else:
        raise HTTPException(400, f"Unknown outcome: {outcome}")

    return {"application_id": application_id, "outcome": outcome}


@router.post("/students/{code}/deliver-pending")
def deliver_pending_messages(code: str) -> dict:
    """Flush all delayed messages for a student — set deliver_at = now()."""
    student = get_student_by_code(code)
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


@router.post("/students/{code}/note")
def post_admin_note(code: str, payload: dict) -> dict:
    """Inject a system message into the student's inbox — useful for
    smoke-testing inbox rendering and the unread badge."""
    student = get_student_by_code(code)
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

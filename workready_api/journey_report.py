"""Lecturer journey report.

Produces a structured, lecturer-friendly summary of a single application:
every stage the student went through, with scores, qualitative feedback,
and key moments. Designed to be readable as a printable grading document
rather than a debug dump.

Deliberate non-goals:
- **No aggregate grade.** Each stage carries its own score from the
  simulation's assessors, but the lecturer makes the final grading
  call. We don't compute a weighted total — that would imply the
  simulation knows how to weight reflection vs. task delivery vs.
  resume tailoring, and it doesn't.
- **No qualitative judgement layer.** The report surfaces what the
  simulation already produced (assessor feedback, mentor notes, exit
  interview summary). We don't re-summarise — that would compound the
  LLM error bars on top of each other.

Stages reflected:
1. Application meta (student, company, role, dates, status)
2. Resume — score, strengths, gaps
3. Hiring interview — score, transcript turn count, key strengths/gaps
4. Work tasks — per-task score, mentor summary, growth areas, late flag
5. Lunchroom — sessions, occasion, status, participation notes (the
   private observation feeds), system feedback (the warm message that
   landed in the inbox)
6. Exit interview — reflection score, transcript turn count, summary

Each section reports `present: bool` so the lecturer can tell at a
glance which stages a student actually reached. A partial report (e.g.
student is mid-placement) is fully valid — it just shows the stages so
far.
"""

from __future__ import annotations

from typing import Any

from workready_api.db import (
    get_active_performance_review,
    get_application,
    get_db,
    get_latest_submission,
    get_stage_results,
    get_student_by_id,
    list_lunchroom_sessions_for_application,
    list_tasks_for_application,
)
from workready_api.jobs import get_job


def build_journey_report(application_id: int) -> dict[str, Any] | None:
    """Build a structured journey report for a single application.

    Returns None if the application doesn't exist. Otherwise returns
    a dict with these top-level keys:
    - meta: student + company + role + dates + status
    - resume: stage 2 summary
    - interview: stage 3 summary
    - tasks: stage 4 summary
    - lunchroom: stage 5 summary
    - exit_interview: stage 6 summary
    - timeline: chronological list of key events for a quick scan
    """
    app_data = get_application(application_id)
    if not app_data:
        return None

    student = get_student_by_id(app_data["student_id"]) or {}
    job = get_job(app_data["company_slug"], app_data.get("job_slug", "")) or {}

    meta = {
        "application_id": application_id,
        "student_name": student.get("name", ""),
        "student_email": student.get("email", ""),
        "company_slug": app_data["company_slug"],
        "company_name": job.get("company", app_data["company_slug"]),
        "job_title": app_data.get("job_title", ""),
        "current_stage": app_data.get("current_stage", ""),
        "status": app_data.get("status", ""),
        "cycle": app_data.get("cycle", 1),
        "created_at": app_data.get("created_at", ""),
        "updated_at": app_data.get("updated_at", ""),
    }

    return {
        "meta": meta,
        "resume": _build_resume_section(application_id),
        "interview": _build_interview_section(application_id),
        "tasks": _build_tasks_section(application_id),
        "performance_review": _build_perf_review_section(application_id),
        "lunchroom": _build_lunchroom_section(application_id),
        "exit_interview": _build_exit_section(application_id),
        "timeline": _build_timeline(application_id),
    }


def _build_perf_review_section(application_id: int) -> dict[str, Any]:
    sess = get_active_performance_review(application_id)
    if not sess:
        return {"present": False}
    feedback = sess.get("feedback") or {}
    return {
        "present": True,
        "status": sess.get("status"),
        "responsiveness_score": sess.get("final_score"),
        "coaching_notes": feedback.get("summary", ""),
        "key_focus": feedback.get("key_focus", ""),
        "transcript_turns": sum(
            1 for m in (sess.get("transcript") or []) if m.get("role") == "user"
        ),
        "completed_at": sess.get("completed_at"),
    }


# --- Stage sections -------------------------------------------------------


def _build_resume_section(application_id: int) -> dict[str, Any]:
    results = get_stage_results(application_id, "resume")
    if not results:
        return {"present": False}
    latest = results[-1]
    feedback = latest.get("feedback") or {}
    return {
        "present": True,
        "attempts": len(results),
        "score": latest.get("score"),
        "status": latest.get("status"),
        "strengths": feedback.get("strengths", []),
        "gaps": feedback.get("gaps", []),
        "suggestions": feedback.get("suggestions", []),
        "tailoring": feedback.get("tailoring", ""),
        "submitted_at": latest.get("created_at"),
    }


def _build_interview_section(application_id: int) -> dict[str, Any]:
    results = get_stage_results(application_id, "interview")
    if not results:
        return {"present": False}
    latest = results[-1]
    feedback = latest.get("feedback") or {}

    # Pull the corresponding hiring interview session for transcript depth
    with get_db() as conn:
        row = conn.execute(
            "SELECT id, transcript_json, final_score, completed_at "
            "FROM interview_sessions "
            "WHERE application_id = ? AND kind = 'hiring' "
            "ORDER BY id DESC LIMIT 1",
            (application_id,),
        ).fetchone()

    transcript_turns = 0
    student_words = 0
    if row:
        import json
        transcript = json.loads(row["transcript_json"] or "[]")
        student_msgs = [m for m in transcript if m.get("role") == "user"]
        transcript_turns = len(student_msgs)
        student_words = sum(len(m.get("content", "").split()) for m in student_msgs)

    return {
        "present": True,
        "attempts": len(results),
        "score": latest.get("score"),
        "status": latest.get("status"),
        "strengths": feedback.get("strengths", []),
        "gaps": feedback.get("gaps", []),
        "suggestions": feedback.get("suggestions", []),
        "tailoring": feedback.get("tailoring", ""),
        "transcript_turns": transcript_turns,
        "student_word_count": student_words,
        "completed_at": latest.get("created_at"),
    }


def _build_tasks_section(application_id: int) -> dict[str, Any]:
    tasks = list_tasks_for_application(application_id, only_visible=False)
    if not tasks:
        return {"present": False}

    task_summaries = []
    for t in tasks:
        sub = get_latest_submission(t["id"]) or {}
        sub_feedback = sub.get("feedback") or {}
        task_summaries.append({
            "sequence": t.get("sequence"),
            "title": t.get("title"),
            "difficulty": t.get("difficulty"),
            "status": t.get("status"),
            "due_at": t.get("due_at"),
            "submitted_at": sub.get("created_at"),
            "score": sub.get("score"),
            "outcome": sub_feedback.get("outcome") or sub.get("review_status"),
            "summary": sub_feedback.get("summary"),
            "strengths": sub_feedback.get("strengths", []),
            "growth_areas": sub_feedback.get("growth_areas", [])
                or sub_feedback.get("improvements", []),
            "late": _was_submission_late(t, sub),
        })

    scored = [t for t in task_summaries if t.get("score") is not None]
    avg_score = (
        sum(t["score"] for t in scored) / len(scored) if scored else None
    )

    return {
        "present": True,
        "task_count": len(tasks),
        "submitted_count": sum(
            1 for t in task_summaries if t.get("submitted_at")
        ),
        "average_score": round(avg_score, 1) if avg_score is not None else None,
        "tasks": task_summaries,
    }


def _was_submission_late(task: dict, sub: dict) -> bool:
    due = task.get("due_at")
    submitted = sub.get("created_at")
    if not due or not submitted:
        return False
    return submitted > due


def _build_lunchroom_section(application_id: int) -> dict[str, Any]:
    sessions = list_lunchroom_sessions_for_application(application_id)
    if not sessions:
        return {"present": False}

    summaries = []
    for sess in sessions:
        summaries.append({
            "occasion": sess.get("occasion"),
            "occasion_detail": sess.get("occasion_detail"),
            "status": sess.get("status"),
            "scheduled_at": sess.get("scheduled_at"),
            "participants": [
                p.get("name", "") for p in (sess.get("participants") or [])
            ],
            "participation_notes": sess.get("participation_notes"),
            "system_feedback": sess.get("system_feedback"),
            "completed_at": sess.get("completed_at"),
        })

    completed = [s for s in summaries if s["status"] == "completed"]
    declined_or_missed = [
        s for s in summaries if s["status"] in ("declined", "missed")
    ]

    return {
        "present": True,
        "session_count": len(sessions),
        "completed_count": len(completed),
        "declined_or_missed_count": len(declined_or_missed),
        "sessions": summaries,
    }


def _build_exit_section(application_id: int) -> dict[str, Any]:
    results = get_stage_results(application_id, "exit_interview")
    with get_db() as conn:
        row = conn.execute(
            "SELECT id, transcript_json, final_score, feedback_json, "
            "completed_at FROM interview_sessions "
            "WHERE application_id = ? AND kind = 'exit' "
            "ORDER BY id DESC LIMIT 1",
            (application_id,),
        ).fetchone()

    if not row and not results:
        return {"present": False}

    summary = ""
    strengths: list[str] = []
    growth_areas: list[str] = []
    score = None
    transcript_turns = 0
    student_words = 0

    if row:
        import json
        score = row["final_score"]
        feedback = json.loads(row["feedback_json"] or "{}")
        summary = feedback.get("summary", "")
        inner = feedback.get("feedback") or {}
        strengths = inner.get("strengths", [])
        growth_areas = inner.get("gaps", []) or inner.get("growth_areas", [])
        transcript = json.loads(row["transcript_json"] or "[]")
        student_msgs = [m for m in transcript if m.get("role") == "user"]
        transcript_turns = len(student_msgs)
        student_words = sum(len(m.get("content", "").split()) for m in student_msgs)

    return {
        "present": True,
        "score": score,
        "summary": summary,
        "strengths": strengths,
        "growth_areas": growth_areas,
        "transcript_turns": transcript_turns,
        "student_word_count": student_words,
        "completed_at": row["completed_at"] if row else None,
    }


# --- Timeline -------------------------------------------------------------


def _build_timeline(application_id: int) -> list[dict[str, Any]]:
    """Build a chronological list of key journey events for quick scanning.

    Each event: {when, kind, label}. Sorted oldest-first. The lecturer
    can scroll the timeline before diving into individual sections.
    """
    events: list[dict[str, Any]] = []

    for r in get_stage_results(application_id):
        events.append({
            "when": r.get("created_at", ""),
            "kind": f"stage:{r.get('stage', '')}",
            "label": (
                f"{r.get('stage', '')} {r.get('status', '')}"
                + (f" ({r.get('score')}/100)" if r.get("score") is not None else "")
            ),
        })

    for t in list_tasks_for_application(application_id, only_visible=False):
        sub = get_latest_submission(t["id"]) or {}
        if sub.get("created_at"):
            events.append({
                "when": sub["created_at"],
                "kind": "task_submission",
                "label": (
                    f"submitted task {t.get('sequence', '?')}: "
                    f"{t.get('title', '')}"
                    + (f" ({sub.get('score')}/100)" if sub.get("score") is not None else "")
                ),
            })

    for sess in list_lunchroom_sessions_for_application(application_id):
        events.append({
            "when": sess.get("created_at", ""),
            "kind": f"lunchroom:{sess.get('status', '')}",
            "label": f"lunchroom {sess.get('status', '?')} — {sess.get('occasion', '')}",
        })

    events.sort(key=lambda e: e["when"])
    return events

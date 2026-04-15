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

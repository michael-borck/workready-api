"""Stage 4 — Work placement activation.

Called when a student passes their interview. Assigns 3 progressively
harder tasks from the company's task_templates, sends the welcome email
from the mentor, and reveals the first task. Tasks 2 and 3 remain gated
(visible_at IS NULL) until the prior task is submitted — see
reveal_next_task_after_submission().

The first task's visibility, the welcome email, and the mentor's first
brief all share the same deliver_at so they land together — using the
same lazy-delivery pattern as messages.deliver_at.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from workready_api import scheduling
from workready_api.db import (
    create_message,
    create_task,
    get_application,
    get_next_gated_task,
    get_student_by_id,
    list_task_templates_for_company,
    list_tasks_for_application,
    reveal_task,
    set_application_status,
    upsert_calendar_event_for_task,
)
from workready_api.jobs import get_job


TASK_SEQUENCE_DIFFICULTIES = ["easy", "medium", "hard"]


def _pick_templates_for_sequence(company_slug: str) -> list[dict[str, Any]]:
    """Pick one template per difficulty tier, in order easy → medium → hard.

    Returns up to TASKS_PER_STUDENT templates. If a difficulty has no
    templates, that slot is skipped. If fewer than TASKS_PER_STUDENT
    templates exist across all tiers, returns what's available.
    """
    picked: list[dict[str, Any]] = []
    used_ids: set[int] = set()
    target_count = scheduling.TASKS_PER_STUDENT

    # Pass 1: one per difficulty tier in order
    for difficulty in TASK_SEQUENCE_DIFFICULTIES[:target_count]:
        candidates = list_task_templates_for_company(company_slug, difficulty)
        for tpl in candidates:
            if tpl["id"] not in used_ids:
                picked.append(tpl)
                used_ids.add(tpl["id"])
                break

    # Pass 2: if TASKS_PER_STUDENT > 3 or a tier was empty, top up with
    # whatever templates remain (preference: hardest available first).
    if len(picked) < target_count:
        all_templates = list_task_templates_for_company(company_slug)
        # Sort so 'hard' comes first, then 'medium', then 'easy' for top-up
        rank = {"hard": 0, "medium": 1, "easy": 2}
        all_templates.sort(key=lambda t: rank.get(t.get("difficulty", ""), 3))
        for tpl in all_templates:
            if len(picked) >= target_count:
                break
            if tpl["id"] not in used_ids:
                picked.append(tpl)
                used_ids.add(tpl["id"])

    return picked


def _compute_due_at(visible_at_iso: str) -> str:
    """Compute due_at = visible_at + TASK_DEADLINE_DAYS."""
    visible = scheduling.from_iso(visible_at_iso)
    due = visible + timedelta(days=scheduling.TASK_DEADLINE_DAYS)
    return scheduling.to_iso(due)


def activate_work_placement(application_id: int, deliver_at: str) -> None:
    """Turn a passed interview into an active work placement.

    - Flip the application status to 'hired'.
    - Create all TASKS_PER_STUDENT task rows upfront; task 1 gets
      visible_at = deliver_at, tasks 2..N stay gated (visible_at NULL).
    - Send the mentor's welcome email AND the first task brief to the
      work inbox, both with deliver_at so they arrive together.

    Idempotent: if tasks already exist for this application, only the
    status flip runs. (Prevents double-creation if interview_end fires
    twice or on retry.)
    """
    app_data = get_application(application_id)
    if not app_data:
        return

    existing = list_tasks_for_application(application_id, only_visible=False)
    if existing:
        # Already activated — just ensure status is right and return.
        set_application_status(application_id, "hired")
        return

    company_slug = app_data["company_slug"]
    job_slug = app_data["job_slug"]
    job = get_job(company_slug, job_slug) or {}
    company_name = job.get("company", company_slug)
    mentor_name = job.get("reports_to", "Your mentor")
    mentor_persona_full = job.get("manager_persona", "") or ""

    templates = _pick_templates_for_sequence(company_slug)
    if not templates:
        # No templates configured for this company — can't activate tasks,
        # but still flip the status so the sidebar knows.
        set_application_status(application_id, "hired")
        import logging
        logging.getLogger(__name__).warning(
            "placement.activate: no task templates for company_slug=%s "
            "(application_id=%d) — skipping task creation",
            company_slug, application_id,
        )
        return

    set_application_status(application_id, "hired")

    first_task_id: int | None = None
    first_task_template: dict[str, Any] | None = None

    for idx, tpl in enumerate(templates, start=1):
        if idx == 1:
            visible_at: str | None = deliver_at
            due_at: str | None = _compute_due_at(deliver_at)
        else:
            visible_at = None
            due_at = None

        task_id = create_task(
            application_id=application_id,
            task_template_id=tpl["id"],
            title=tpl["title"],
            brief=tpl["brief"],
            description=tpl["description"],
            difficulty=tpl["difficulty"],
            sequence=idx,
            visible_at=visible_at,
            due_at=due_at,
        )
        # Task 1 is the only one with a known deadline at placement time.
        # Tasks 2 and 3 get their calendar event when they're revealed.
        if due_at:
            upsert_calendar_event_for_task(
                application_id=application_id,
                task_id=task_id,
                title=f"Task due: {tpl['title']}",
                scheduled_at=due_at,
                description=tpl["brief"],
            )
        if idx == 1:
            first_task_id = task_id
            first_task_template = tpl

    # Send mentor welcome + first-task brief emails, both delayed to deliver_at
    student = get_student_by_id(app_data["student_id"])
    if not student:
        return

    total_tasks = len(templates)
    welcome_body = (
        f"Hi {student['name'].split()[0] if student['name'] else 'there'},\n\n"
        f"Welcome to {company_name} — I'm {mentor_name}, and I'll be your "
        f"mentor during your internship here. Really glad to have you on "
        f"the team.\n\n"
        f"Here's how it'll work. Over the course of your time with us, "
        f"you'll complete {total_tasks} progressively more challenging "
        f"tasks. I've just sent through your first brief — have a look "
        f"when you're ready. As you submit each task, the next one will "
        f"appear in your task list, and a little while later you'll "
        f"receive my feedback on the work you just submitted. Use that "
        f"feedback to strengthen the task you're currently working on — "
        f"that's how real internships work, and it's how you'll get the "
        f"most out of this.\n\n"
        f"Don't hesitate to reach out if you need clarification on "
        f"anything. Good luck with task one.\n\n"
        f"— {mentor_name}"
    )
    create_message(
        student_id=student["id"],
        student_email=student["email"],
        sender_name=mentor_name,
        sender_role=f"Your mentor at {company_name}",
        subject=f"Welcome to {company_name} — your internship is on",
        body=welcome_body,
        inbox="work",
        application_id=application_id,
        related_stage="placement",
        deliver_at=deliver_at,
    )

    if first_task_template:
        brief_body = (
            f"Hi {student['name'].split()[0] if student['name'] else 'there'},\n\n"
            f"Here's your first task — a gentle start to get you into the "
            f"rhythm of how we work.\n\n"
            f"TASK: {first_task_template['title']}\n"
            f"DIFFICULTY: {first_task_template['difficulty']}\n\n"
            f"{first_task_template['brief']}\n\n"
            f"You'll find the full brief in your task list. Take your "
            f"time, do it properly, and submit when you're ready. I'll "
            f"review it and get back to you with feedback shortly "
            f"afterwards.\n\n"
            f"— {mentor_name}"
        )
        create_message(
            student_id=student["id"],
            student_email=student["email"],
            sender_name=mentor_name,
            sender_role=f"Your mentor at {company_name}",
            subject=f"Your first task — {first_task_template['title']}",
            body=brief_body,
            inbox="work",
            application_id=application_id,
            related_stage="placement",
            deliver_at=deliver_at,
        )


def reveal_next_task_after_submission(application_id: int) -> dict[str, Any] | None:
    """Called after a task is submitted — flip the next gated task visible.

    The next task becomes visible after TASK_NEXT_TASK_DELAY_MINUTES
    (+ jitter). Deadline is computed from that reveal time. Sends a
    mentor email announcing the next brief with the same delay so it
    lands as the task appears.

    Returns the revealed task dict, or None if no gated task exists
    (i.e., this was the final task).
    """
    next_task = get_next_gated_task(application_id)
    if not next_task:
        return None

    app_data = get_application(application_id)
    if not app_data:
        return None

    reveal_at = scheduling.feedback_delivery_time(
        scheduling.TASK_NEXT_TASK_DELAY_MINUTES,
        scheduling.TASK_NEXT_TASK_DELAY_JITTER_MINUTES,
    )
    due_at = _compute_due_at(reveal_at)
    reveal_task(next_task["id"], reveal_at, due_at)
    upsert_calendar_event_for_task(
        application_id=application_id,
        task_id=next_task["id"],
        title=f"Task due: {next_task['title']}",
        scheduled_at=due_at,
        description=next_task["brief"],
    )

    # Send the mentor's "here's your next brief" email
    job = get_job(app_data["company_slug"], app_data["job_slug"]) or {}
    company_name = job.get("company", app_data["company_slug"])
    mentor_name = job.get("reports_to", "Your mentor")
    student = get_student_by_id(app_data["student_id"])
    if not student:
        return next_task

    first_name = student["name"].split()[0] if student["name"] else "there"
    brief_body = (
        f"Hi {first_name},\n\n"
        f"Good work wrapping up the last one. Here's your next task.\n\n"
        f"TASK: {next_task['title']}\n"
        f"DIFFICULTY: {next_task['difficulty']}\n\n"
        f"{next_task['brief']}\n\n"
        f"Full brief is in your task list. My feedback on the previous "
        f"task will land in your inbox shortly — when it does, have a "
        f"read and see if there's anything you can carry forward into "
        f"this one.\n\n"
        f"— {mentor_name}"
    )
    create_message(
        student_id=student["id"],
        student_email=student["email"],
        sender_name=mentor_name,
        sender_role=f"Your mentor at {company_name}",
        subject=f"Next task — {next_task['title']}",
        body=brief_body,
        inbox="work",
        application_id=application_id,
        related_stage="placement",
        deliver_at=reveal_at,
    )

    return next_task

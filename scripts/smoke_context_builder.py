#!/usr/bin/env python3
"""Smoke test: context_builder.

Seeds a student with applications in different stages, inserts messages
and task rows, then asserts build_character_context returns the right
shape and content.
"""

import asyncio
import os
import pathlib

os.environ.setdefault("WORKREADY_DB", "/tmp/smoke_context.db")
os.environ.setdefault("LLM_PROVIDER", "stub")
pathlib.Path(os.environ["WORKREADY_DB"]).unlink(missing_ok=True)

from workready_api.db import (
    init_db, get_or_create_student, create_application, create_message,
    record_stage_result, get_db, advance_stage,
    create_outbound_message,
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
    "company": "Context Test Co",
}

s = get_or_create_student("ctx@example.com", "Alex Tester")
app_id = create_application(
    student_id=s["id"], student_email="ctx@example.com",
    company_slug="ctx-test", job_slug="analyst", job_title="Analyst",
)

# Seed prior stage results (record_stage_result overwrites current_stage,
# so advance_stage must come after to ensure current_stage == "work_task")
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
advance_stage(app_id, "work_task")

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
    inbox="work", application_id=app_id, related_stage="work_task",
)
create_outbound_message(
    student_id=s["id"], student_email="ctx@example.com",
    recipient_email="karen.whitfield@contexttest.com.au",
    subject="Re: Welcome!", body="Thanks Karen, quick question about task 1...",
)

from workready_api.context_builder import build_character_context

ctx = asyncio.run(build_character_context(
    student_id=s["id"],
    character_slug="karen-whitfield",
    application_id=app_id,
))

# --- Assertions ---
assert ctx.student_first_name == "Alex", f"got {ctx.student_first_name}"
assert ctx.company_name == "Context Test Co"
assert ctx.job_title == "Analyst"
assert ctx.current_stage == "work_task"
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
for i in range(40):
    create_message(
        student_id=s["id"], student_email="ctx@example.com",
        sender_name="Karen Whitfield", sender_role="Ops Lead",
        subject=f"Msg {i}", body="x" * 800,
        inbox="work", application_id=app_id, related_stage="work_task",
    )

ctx2 = asyncio.run(build_character_context(
    student_id=s["id"],
    character_slug="karen-whitfield",
    application_id=app_id,
))
assert ctx2.thread_summary, "thread_summary should be populated after overflow"
assert len(ctx2.thread) <= 10, f"tail should be short, got {len(ctx2.thread)}"
print(f"  after overflow: tail={len(ctx2.thread)} summary_len={len(ctx2.thread_summary)}")

print("\nOK: context_builder smoke passed")

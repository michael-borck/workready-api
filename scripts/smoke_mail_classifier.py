#!/usr/bin/env python3
"""Smoke test: mail.py bounce-back paths.

Calls _schedule_bounceback directly with pre-built flagged
ClassificationResults (since stub-mode classifier always returns ok).
Asserts that the right proxy message lands in the right inbox.
"""

import os
import pathlib

os.environ.setdefault("WORKREADY_DB", "/tmp/smoke_mail.db")
os.environ.setdefault("LLM_PROVIDER", "stub")
pathlib.Path(os.environ["WORKREADY_DB"]).unlink(missing_ok=True)

from workready_api.db import (
    init_db, get_or_create_student, create_application, advance_stage,
    get_db,
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
    "company": "Mail Test Co",
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
_schedule_bounceback(
    classification=flag_audience,
    student=s,
    application_id=app_id,
    app_data={"company_slug": "mail-test", "job_slug": "analyst",
              "current_stage": "placement"},
    recipient_email="ceo@mailtest.com.au",
    subject="Where's the coffee machine?",
    inbox="work",
)

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
_schedule_bounceback(
    classification=flag_tone,
    student=s,
    application_id=app_id,
    app_data={"company_slug": "mail-test", "job_slug": "analyst",
              "current_stage": "placement"},
    recipient_email="karen.whitfield@mailtest.com.au",
    subject="Task 2",
    inbox="work",
)

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
_schedule_bounceback(
    classification=flag_channel,
    student=s,
    application_id=app_id,
    app_data={"company_slug": "mail-test", "job_slug": "analyst",
              "current_stage": "placement"},
    recipient_email="karen.whitfield@mailtest.com.au",
    subject="Quick question",
    inbox="personal",
)

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

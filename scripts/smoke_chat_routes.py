#!/usr/bin/env python3
"""Smoke test: chat routes end-to-end.

Boots the API against a seeded DB, sends a chat message via
POST /chat/send, polls GET /chat/thread, asserts messages appear.
"""

import os
import pathlib
import subprocess
import time

os.environ.setdefault("WORKREADY_DB", "/tmp/smoke_chat.db")
os.environ.setdefault("LLM_PROVIDER", "stub")
pathlib.Path(os.environ["WORKREADY_DB"]).unlink(missing_ok=True)

from workready_api.db import (
    init_db, get_or_create_student, create_application, advance_stage,
)

init_db()

# Use a real company/job/employee so the server's _COMPANY_CACHE is populated
# at startup from the nexuspoint-systems/jobs.json file.
COMPANY_SLUG = "nexuspoint-systems"
JOB_SLUG = "service-desk-analyst"
JOB_TITLE = "Service Desk Analyst"
CHARACTER_SLUG = "sam-okoro"
CHARACTER_NAME = "Sam Okoro"

s = get_or_create_student("chat@example.com", "Alex Tester")
app_id = create_application(
    student_id=s["id"], student_email="chat@example.com",
    company_slug=COMPANY_SLUG, job_slug=JOB_SLUG, job_title=JOB_TITLE,
)
advance_stage(app_id, "placement")

proc = subprocess.Popen(
    ["uv", "run", "uvicorn", "workready_api.app:app",
     "--port", "8702", "--log-level", "warning"],
    env={**os.environ},
)
try:
    time.sleep(3)

    import httpx
    base = "http://127.0.0.1:8702"

    # --- Test 1: POST /chat/send ---
    r = httpx.post(f"{base}/api/v1/chat/send", json={
        "application_id": app_id,
        "character_slug": CHARACTER_SLUG,
        "content": "Hey Sam, quick question about task 1",
    }, timeout=10)
    assert r.status_code == 200, f"send failed: {r.status_code} {r.text}"
    result = r.json()
    assert result.get("flagged") is False
    assert "message_id" in result
    print(f"  [1/3] chat/send succeeded, message_id={result['message_id']}")

    # --- Test 2: GET /chat/thread shows at least the student message ---
    time.sleep(1)
    r = httpx.get(f"{base}/api/v1/chat/thread/{app_id}/{CHARACTER_SLUG}")
    assert r.status_code == 200, f"thread failed: {r.status_code} {r.text}"
    thread = r.json()
    student_msgs = [m for m in thread["messages"] if m["author"] == "student"]
    assert len(student_msgs) >= 1, f"No student messages in thread: {thread}"
    print(f"  [2/3] chat/thread returned {len(thread['messages'])} messages")

    # --- Test 3: Thread shape includes character metadata ---
    assert thread["character_name"] == CHARACTER_NAME, (
        f"Expected '{CHARACTER_NAME}', got '{thread['character_name']}'"
    )
    assert thread["presence_ok"] is True
    print(f"  [3/3] thread metadata correct (character_name, presence_ok)")

finally:
    proc.terminate()
    proc.wait(timeout=5)

print("\nOK: chat routes smoke passed")

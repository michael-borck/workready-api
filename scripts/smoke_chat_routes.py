#!/usr/bin/env python3
"""Smoke test: chat routes end-to-end.

Boots the API against a seeded DB, sends a chat message via
POST /chat/send, polls GET /chat/thread, asserts messages appear.
"""

import os
import pathlib
import subprocess
import time
import tempfile

_temp = tempfile.TemporaryDirectory(prefix='workready-chat-smoke-')
os.environ['WORKREADY_DB'] = str(pathlib.Path(_temp.name) / 'smoke.db')
os.environ.setdefault("LLM_PROVIDER", "stub")

from workready_api.db import (
    init_db, get_or_create_student, generate_codes, create_application, advance_stage,
)

init_db()

# Use a real company/job/employee so the server's _COMPANY_CACHE is populated
# at startup from the nexuspoint-systems/jobs.json file.
COMPANY_SLUG = "nexuspoint-systems"
JOB_SLUG = "service-desk-analyst"
JOB_TITLE = "Service Desk Analyst"
CHARACTER_SLUG = "sam-okoro"
CHARACTER_NAME = "Sam Okoro"

_code = generate_codes(1)[0]
s = get_or_create_student(_code)
app_id = create_application(
    student_id=s["id"], company_slug=COMPANY_SLUG, job_slug=JOB_SLUG, job_title=JOB_TITLE,
)
advance_stage(app_id, "placement")

proc = subprocess.Popen(
    ["uv", "run", "uvicorn", "workready_api.app:app",
     "--port", "8702", "--log-level", "warning"],
    env={**os.environ},
)
try:
    import httpx
    base = "http://127.0.0.1:8702"

    # Wait for the server to accept requests (cold start can be slow)
    for _ in range(30):
        try:
            httpx.get(f"{base}/api/v1/postings", timeout=2)
            break
        except httpx.ConnectError:
            time.sleep(1)


    # --- Test 1: POST /chat/send ---
    login = httpx.post(f'{base}/api/v1/auth/login', json={'code': _code}).json()
    headers = {'Authorization': 'Bearer ' + login['token']}
    r = httpx.post(f"{base}/api/v1/chat/send", json={
        "application_id": app_id,
        "character_slug": CHARACTER_SLUG,
        "content": "Hey Sam, quick question about task 1",
    }, headers=headers, timeout=10)
    assert r.status_code == 200, f"send failed: {r.status_code} {r.text}"
    result = r.json()
    assert result.get("flagged") is False
    assert "message_id" in result
    print(f"  [1/3] chat/send succeeded, message_id={result['message_id']}")

    # --- Test 2: GET /chat/thread shows at least the student message ---
    time.sleep(1)
    r = httpx.get(f"{base}/api/v1/chat/thread/{app_id}/{CHARACTER_SLUG}", headers=headers)
    assert r.status_code == 200, f"thread failed: {r.status_code} {r.text}"
    thread = r.json()
    student_msgs = [m for m in thread["messages"] if m["author"] == "student"]
    assert len(student_msgs) >= 1, f"No student messages in thread: {thread}"
    print(f"  [2/3] chat/thread returned {len(thread['messages'])} messages")

    # --- Test 3: Thread shape includes character metadata ---
    assert thread["character_name"] == CHARACTER_NAME, (
        f"Expected '{CHARACTER_NAME}', got '{thread['character_name']}'"
    )
    # presence_ok reflects simulated business hours — only meaningful
    # to assert when "now" falls inside them (Mon-Fri 09:00-17:00 local)
    import datetime as _dt
    _now_local = _dt.datetime.now()
    _in_hours = _now_local.weekday() < 5 and 9 <= _now_local.hour < 17
    if _in_hours:
        assert thread["presence_ok"] is True, (
            f"expected present during business hours, got {thread['presence_ok']}"
        )
        print(f"  [3/3] thread metadata correct (character_name, presence_ok)")
    else:
        print(f"  [3/3] thread metadata ok (presence_ok={thread['presence_ok']} — "
              f"outside business hours, not asserted)")

finally:
    proc.terminate()
    proc.wait(timeout=5)
    _temp.cleanup()

print("\nOK: chat routes smoke passed")

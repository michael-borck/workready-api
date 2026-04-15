#!/usr/bin/env python3
"""Smoke test: team_directory resolver.

Covers three scenarios:
1. Job with explicit team: field → resolves to that subset
2. Job with no team: field → falls back to whole employees list
3. Unknown application_id → empty result, no crash
"""

import os
import pathlib

os.environ.setdefault("WORKREADY_DB", "/tmp/smoke_team.db")
os.environ.setdefault("LLM_PROVIDER", "stub")
pathlib.Path(os.environ["WORKREADY_DB"]).unlink(missing_ok=True)

from workready_api.db import (
    init_db, get_or_create_student, create_application,
)
from workready_api.jobs import _COMPANY_CACHE, _JOB_CACHE

init_db()

# --- Seed a fake company + job with an explicit team ---
_COMPANY_CACHE["test-explicit"] = {
    "company": "Explicit Team Co",
    "domain": "explicitteam.com.au",
    "business_hours": {"start": 9, "end": 17, "days": [1, 2, 3, 4, 5]},
    "employees": [
        {"slug": "alice", "name": "Alice", "role": "Manager"},
        {"slug": "bob", "name": "Bob", "role": "Analyst"},
        {"slug": "carol", "name": "Carol", "role": "Engineer"},
        {"slug": "dave", "name": "Dave", "role": "CEO"},
    ],
}
_JOB_CACHE[("test-explicit", "junior")] = {
    "slug": "junior",
    "title": "Junior",
    "team": ["alice", "bob"],
}

# --- Seed a company with no team[] field on the job ---
_COMPANY_CACHE["test-default"] = {
    "company": "Default Team Co",
    "domain": "defaultteam.com.au",
    "business_hours": {"start": 9, "end": 17, "days": [1, 2, 3, 4, 5]},
    "employees": [
        {"slug": "eve", "name": "Eve", "role": "Owner"},
        {"slug": "frank", "name": "Frank", "role": "Lead"},
    ],
}
_JOB_CACHE[("test-default", "intern")] = {
    "slug": "intern",
    "title": "Intern",
}

# --- Create synthetic applications ---
s = get_or_create_student("t@example.com", "Tester")

app_explicit = create_application(
    student_id=s["id"], student_email="t@example.com",
    company_slug="test-explicit", job_slug="junior", job_title="Junior",
)
app_default = create_application(
    student_id=s["id"], student_email="t@example.com",
    company_slug="test-default", job_slug="intern", job_title="Intern",
)

from workready_api.team_directory import get_team_for_application

# --- Test 1: explicit team[] is respected ---
r1 = get_team_for_application(app_explicit)
team_slugs = [m["slug"] for m in r1["team"]]
org_slugs = [m["slug"] for m in r1["org"]]
assert set(team_slugs) == {"alice", "bob"}, f"Got {team_slugs}"
assert set(org_slugs) == {"carol", "dave"}, f"Got {org_slugs}"
assert all(m["email_only"] is False for m in r1["team"])
assert all(m["email_only"] is True for m in r1["org"])
print(f"  [1/3] explicit team resolver: team={team_slugs} org={org_slugs}")

# --- Test 2: default fallback is whole employees list ---
r2 = get_team_for_application(app_default)
team_slugs_2 = [m["slug"] for m in r2["team"]]
assert set(team_slugs_2) == {"eve", "frank"}, f"Got {team_slugs_2}"
assert len(r2["org"]) == 0, f"Expected empty org, got {r2['org']}"
print(f"  [2/3] default fallback: team={team_slugs_2} (whole employees list)")

# --- Test 3: unknown application ---
r3 = get_team_for_application(99999)
assert r3 == {"team": [], "org": [], "business_hours": {}}
print("  [3/3] unknown application returns empty shape, no crash")

print("\nOK: team_directory smoke passed all 3 checks")

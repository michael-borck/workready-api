"""Blocking rules — what becomes "off the board" when a student is rejected.

A failed application can block:
- Just the role (the specific job at that company)
- The whole company (every job at that company)
- Nothing (the student can re-apply)

Default rules (recommended for educational realism):
- Resume failure → "role" — the resume reviewer probably doesn't remember you
- Interview failure → "company" — the manager remembers you
- Task failure → "company"

Cohort-wide defaults are set via env vars or config file. Per-job
overrides can be declared in brief.yaml and passed through jobs.json.
"""

from __future__ import annotations

import os
from typing import Literal

from workready_api.db import get_db

BlockingLevel = Literal["none", "role", "company"]


def _env_level(name: str, default: BlockingLevel) -> BlockingLevel:
    """Read a blocking level from env, validating against allowed values."""
    val = os.environ.get(name, default).lower()
    if val not in ("none", "role", "company"):
        return default
    return val  # type: ignore[return-value]


# Cohort-wide defaults — overridable via env vars
BLOCK_ON_RESUME_FAILURE: BlockingLevel = _env_level("BLOCK_ON_RESUME_FAILURE", "role")
BLOCK_ON_INTERVIEW_FAILURE: BlockingLevel = _env_level("BLOCK_ON_INTERVIEW_FAILURE", "company")
BLOCK_ON_TASK_FAILURE: BlockingLevel = _env_level("BLOCK_ON_TASK_FAILURE", "company")


def get_blocking_level_for_stage(stage: str) -> BlockingLevel:
    """Return the configured blocking level for a stage failure."""
    return {
        "resume": BLOCK_ON_RESUME_FAILURE,
        "interview": BLOCK_ON_INTERVIEW_FAILURE,
        "placement": BLOCK_ON_TASK_FAILURE,
    }.get(stage, "company")


def get_blocked_for_student(student_id: int) -> dict:
    """Compute what's blocked for a student.

    Returns:
        {
            "companies": [list of company_slugs blocked at company level],
            "jobs": [list of {company_slug, job_slug} blocked at role level],
        }

    The portal/seek.jobs uses these to grey out postings:
    - If a posting's company is in `companies`, grey it out
    - Else if the (company, job) is in `jobs`, grey it out
    - Else show as available
    """
    blocked_companies: set[str] = set()
    blocked_jobs: set[tuple[str, str]] = set()

    with get_db() as conn:
        # Find all rejected applications and the stage they failed at
        rows = conn.execute(
            """SELECT a.company_slug, a.job_slug, sr.stage
               FROM applications a
               JOIN stage_results sr ON sr.application_id = a.id
               WHERE a.student_id = ? AND a.status = 'rejected'
                 AND sr.status = 'failed'""",
            (student_id,),
        ).fetchall()

        # Resigned applications also block the company — you can't quit
        # IronVale on Tuesday and apply back on Wednesday. Same realism
        # the rejection blocking provides. A resign always blocks at
        # company level (the whole org remembers you).
        resigned_rows = conn.execute(
            """SELECT company_slug, job_slug FROM applications
               WHERE student_id = ? AND status = 'resigned'""",
            (student_id,),
        ).fetchall()

        # Completed applications also block re-application to the same
        # company — once you've finished a placement there, you don't
        # restart from scratch (cycle goes to a new company instead).
        completed_rows = conn.execute(
            """SELECT company_slug, job_slug FROM applications
               WHERE student_id = ? AND status = 'completed'""",
            (student_id,),
        ).fetchall()

    for row in rows:
        level = get_blocking_level_for_stage(row["stage"])
        if level == "company":
            blocked_companies.add(row["company_slug"])
        elif level == "role":
            blocked_jobs.add((row["company_slug"], row["job_slug"]))
        # level == "none" → no blocking

    for row in resigned_rows:
        blocked_companies.add(row["company_slug"])
    for row in completed_rows:
        blocked_companies.add(row["company_slug"])

    # If a company is blocked, all roles within it are implicitly blocked.
    # We don't enumerate them here — the consumer checks company first.
    # But remove role-level blocks for companies that are fully blocked
    # (cleanup, not strictly necessary).
    blocked_jobs = {
        (c, j) for (c, j) in blocked_jobs if c not in blocked_companies
    }

    return {
        "companies": sorted(blocked_companies),
        "jobs": sorted([{"company_slug": c, "job_slug": j} for c, j in blocked_jobs],
                       key=lambda x: (x["company_slug"], x["job_slug"])),
    }


def is_posting_blocked(
    company_slug: str,
    job_slug: str,
    blocked: dict,
) -> bool:
    """Check if a specific posting is blocked for a student.

    `blocked` is the dict returned by get_blocked_for_student().
    """
    if company_slug in blocked["companies"]:
        return True
    return any(
        b["company_slug"] == company_slug and b["job_slug"] == job_slug
        for b in blocked["jobs"]
    )

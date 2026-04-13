"""Job data loader — reads jobs.json exports from company sites."""

from __future__ import annotations

import json
from pathlib import Path


# Job descriptions keyed by (company_slug, job_slug)
_JOB_CACHE: dict[tuple[str, str], dict] = {}

# Company-level metadata keyed by company_slug (business_hours, etc.)
_COMPANY_CACHE: dict[str, dict] = {}


def load_jobs(sites_dir: Path, site_slugs: list[str]) -> None:
    """Load all jobs.json files into the cache.

    Supports two layouts:
    - sites_dir/{slug}/jobs.json  (development — site directories)
    - sites_dir/{slug}.json       (container — flat directory of exports)
    """
    _JOB_CACHE.clear()
    _COMPANY_CACHE.clear()
    for slug in site_slugs:
        # Try site directory layout first, then flat layout
        jobs_file = sites_dir / slug / "jobs.json"
        if not jobs_file.is_file():
            jobs_file = sites_dir / f"{slug}.json"
        if not jobs_file.is_file():
            continue
        with open(jobs_file) as f:
            data = json.load(f)

        company_slug = data["company_slug"]
        # Cache company-level metadata (business hours, task templates, etc.)
        company_meta: dict = {
            "company": data["company"],
            "company_slug": company_slug,
            "company_url": data.get("company_url", ""),
        }
        if "business_hours" in data:
            company_meta["business_hours"] = data["business_hours"]
        if "task_templates" in data:
            company_meta["task_templates"] = data["task_templates"]
        _COMPANY_CACHE[company_slug] = company_meta

        for job in data["jobs"]:
            key = (company_slug, job["slug"])
            _JOB_CACHE[key] = {
                "company": data["company"],
                "company_slug": company_slug,
                **job,
            }


def get_company(company_slug: str) -> dict | None:
    """Look up company-level metadata."""
    return _COMPANY_CACHE.get(company_slug)


def get_company_business_hours(company_slug: str) -> dict | None:
    """Return per-company business_hours override dict, or None if not set."""
    company = _COMPANY_CACHE.get(company_slug)
    if not company:
        return None
    return company.get("business_hours")


def get_job(company_slug: str, job_slug: str) -> dict | None:
    """Look up a job by company and job slug."""
    return _JOB_CACHE.get((company_slug, job_slug))


def get_job_description(company_slug: str, job_slug: str) -> str:
    """Get the job description text for assessment."""
    job = get_job(company_slug, job_slug)
    if not job:
        return ""
    return job.get("description", "")


def get_company_task_templates(company_slug: str) -> list[dict]:
    """Return the list of task templates declared in the company brief."""
    company = _COMPANY_CACHE.get(company_slug)
    if not company:
        return []
    return company.get("task_templates", []) or []


def seed_task_templates_from_jobs() -> int:
    """Upsert task templates from loaded jobs into the DB. Returns count seeded.

    Idempotent — uniqueness on (company_slug, title) means re-runs update
    fields rather than duplicating rows.
    """
    from workready_api.db import upsert_task_template

    count = 0
    for company_slug, company in _COMPANY_CACHE.items():
        for tpl in company.get("task_templates", []) or []:
            title = tpl.get("title", "").strip()
            if not title:
                continue
            upsert_task_template(
                company_slug=company_slug,
                title=title,
                brief=tpl.get("brief", ""),
                description=tpl.get("description", ""),
                difficulty=(tpl.get("difficulty") or "medium").lower(),
                discipline=tpl.get("discipline"),
                estimated_hours=int(tpl.get("estimated_hours", 4)),
            )
            count += 1
    return count


def seed_postings_from_jobs() -> int:
    """Create or update postings from loaded jobs. Returns count seeded.

    For every loaded job:
    - Always creates a 'direct' posting (the company's own listing)
    - For any agency listings declared in jobs.json under
      `additional_postings`, creates one posting per agency
    """
    from workready_api.db import upsert_posting

    count = 0
    for (company_slug, job_slug), job in _JOB_CACHE.items():
        title = job.get("title", job_slug)
        description = job.get("description", "")

        # Direct posting
        upsert_posting(
            company_slug=company_slug,
            job_slug=job_slug,
            listing_title=title,
            source_type="direct",
            agency_name=None,
            listing_description=description,
            confidential=False,
        )
        count += 1

        # Agency postings (if declared in jobs.json)
        for ap in job.get("additional_postings", []) or []:
            upsert_posting(
                company_slug=company_slug,
                job_slug=job_slug,
                listing_title=ap.get("title", title),
                source_type="agency",
                agency_name=ap.get("agency", "Recruitment Agency"),
                listing_description=ap.get("description", description),
                confidential=bool(ap.get("confidential", False)),
            )
            count += 1

    return count


def get_interview_pipeline(company_slug: str, job_slug: str) -> list[dict]:
    """Get the interview pipeline for a job.

    Returns the pipeline declared in jobs.json, or a default single-stage
    pipeline using the job's `reports_to` field as the manager.

    Pipeline format:
        [
            {"type": "manager", "with": "marcus-webb"},
            {"type": "technical", "with": "liam-foster", "format": "..."},
            {"type": "panel", "with": ["alex-nguyen", "marcus-webb"]},
        ]

    Supported types: manager, hr_screen, technical, panel, reference
    The MVP only uses 'manager' but the data model supports all of them.
    """
    job = get_job(company_slug, job_slug)
    if not job:
        return []

    pipeline = job.get("interview_pipeline")
    if pipeline:
        return pipeline

    # Default: single-stage interview with the job's reports_to manager
    reports_to = job.get("reports_to") or job.get("manager_slug") or ""
    return [{"type": "manager", "with": reports_to}]

"""Team directory resolver.

Single public function that turns an application_id into two lists:
the student's immediate team (full chat + email) and the wider
organisation (email only).

Written as one function with a clean input/output contract so the
future dynamic-team-adjustment project can slot in an override
precedence chain without touching callers.
"""

from __future__ import annotations

from typing import Any

from workready_api.availability import is_character_available
from workready_api.db import get_application
from workready_api.jobs import get_company, get_job


def get_team_for_application(application_id: int) -> dict[str, Any]:
    """Return the team directory for a student's application.

    Shape:
        {
            "team": [CharacterRef, ...],
            "org":  [CharacterRef, ...],
            "business_hours": {start, end, days, holidays_region},
        }

    Where CharacterRef is:
        {
            "slug": str,
            "name": str,
            "role": str,
            "email": str,
            "presence_ok": bool,
            "availability_status": str,
            "availability_note": str,
            "email_only": bool,
        }

    Returns {"team": [], "org": [], "business_hours": {}} if the
    application or company cannot be resolved.
    """
    app_data = get_application(application_id)
    if not app_data:
        return _empty()

    company_slug = app_data.get("company_slug", "")
    job_slug = app_data.get("job_slug", "")

    company = get_company(company_slug) or {}
    job = get_job(company_slug, job_slug) or {}

    employees = company.get("employees", []) or []
    employees_by_slug = {e.get("slug"): e for e in employees if e.get("slug")}

    team_slugs = _resolve_team_slugs(job, employees)
    team_refs = [
        _build_character_ref(company_slug, employees_by_slug[s], company)
        for s in team_slugs
        if s in employees_by_slug
    ]

    org_slugs = [
        s for s in employees_by_slug.keys()
        if s not in set(team_slugs)
    ]
    org_refs = [
        _build_character_ref(
            company_slug, employees_by_slug[s], company, email_only=True,
        )
        for s in org_slugs
    ]

    return {
        "team": team_refs,
        "org": org_refs,
        "business_hours": company.get("business_hours", {}),
    }


def _empty() -> dict[str, Any]:
    return {"team": [], "org": [], "business_hours": {}}


def _resolve_team_slugs(job: dict, employees: list[dict]) -> list[str]:
    """Determine the student's team from their job.

    Precedence chain (the future dynamic-team-adjustment project will
    extend this):

    1. Job's explicit `team:` field → use it
    2. Otherwise → whole employees[] roster (small-business fallback)

    Returns the resolved team as a list of character slugs.
    """
    explicit = job.get("team", []) or []
    if explicit:
        return list(explicit)
    return [e["slug"] for e in employees if e.get("slug")]


_HONORIFICS = {"mr.", "ms.", "mrs.", "dr.", "prof.", "mx."}


def _first_name_token(display_name: str, slug: str) -> str:
    """Return the first non-honorific name token, lowercased.

    Falls back to the slug if every token is an honorific or the name is empty.
    """
    for part in display_name.lower().split():
        if part not in _HONORIFICS:
            return part
    return slug


def _build_character_ref(
    company_slug: str,
    employee: dict,
    company: dict,
    *,
    email_only: bool = False,
) -> dict[str, Any]:
    """Serialise a brief.yaml employee dict into an API CharacterRef."""
    slug = employee.get("slug", "")
    name = employee.get("name", "")
    role = employee.get("role", "")
    avail = employee.get("availability") or {}
    status = avail.get("status", "available")
    note = avail.get("note", "") or ""

    domain = _domain_for_company(company_slug, company)
    first_name = _first_name_token(name, slug) if name else slug
    email = f"{first_name}.{slug.split('-', 1)[-1]}@{domain}" if slug else ""

    return {
        "slug": slug,
        "name": name,
        "role": role,
        "email": email,
        "presence_ok": False if email_only else is_character_available(company_slug, slug),
        "availability_status": status,
        "availability_note": note,
        "email_only": email_only,
    }


def _domain_for_company(company_slug: str, company: dict) -> str:
    """Return the company domain for email construction.

    Uses the same pattern as setup-chatbots.py and email_registry.py —
    strip hyphens and append .com.au. Future improvement: read from
    brief.yaml company.domain if present.
    """
    configured = company.get("domain")
    if configured:
        return configured
    return company_slug.replace("-", "") + ".com.au"

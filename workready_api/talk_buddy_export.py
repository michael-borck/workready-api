"""Talk Buddy scenario export.

Produces JSON files compatible with Talk Buddy's import format
(formatVersion 2.0 — see talk-buddy/docs/workflows/exporting-sharing.md
and talk-buddy/src/renderer/types/index.ts).

Two surfaces today:

- **Hiring interview** (Stage 3): one scenario per application. Reuses
  `interview.build_interview_system_prompt` so the practice persona is
  identical to the real one — same manager voice, same role context,
  same resume awareness.

- **Lunchroom** (Stage 5): one scenario per AI participant in a session,
  bundled as a skill_package. The student can practise small talk with
  each colleague 1:1 before the real group lunch fires. Persona text is
  loaded from the same `content/employees/{slug}-prompt.txt` files the
  live chat uses.

Talk Buddy is single-character per scenario, so we don't try to model
the multi-voice group chat — bundling individual scenarios is closer to
how students would naturally rehearse anyway ("let me practise with
Karen first, then Ravi").

Future surfaces (exit interview, performance review) follow the same
pattern: one builder per surface, then `wrap_as_package` for the envelope.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from workready_api.db import get_application, get_lunchroom_session, get_stage_results
from workready_api.interview import build_interview_system_prompt
from workready_api.jobs import get_company, get_job
from workready_api import lunchroom_chat


FORMAT_VERSION = "2.0"
EXPORTER_TAG = "WorkReady Simulation"


# --- Envelope helpers -----------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _scenario_id(prefix: str, *parts: str | int) -> str:
    """Deterministic scenario ID so re-exports stay stable."""
    suffix = "-".join(str(p).lower().replace(" ", "-") for p in parts if p)
    return f"workready-{prefix}-{suffix}" if suffix else f"workready-{prefix}"


def wrap_as_package(
    scenarios: list[dict[str, Any]],
    *,
    title: str,
    description: str,
) -> dict[str, Any]:
    """Wrap one or more scenarios in a Talk Buddy skill_package envelope."""
    return {
        "formatVersion": FORMAT_VERSION,
        "type": "skill_package",
        "metadata": {
            "exportedBy": EXPORTER_TAG,
            "exportDate": _now_iso(),
            "title": title,
            "description": description,
            "scenarioCount": len(scenarios),
        },
        "package": {
            "name": title,
            "description": description,
            "scenarios": scenarios,
        },
    }


# --- Hiring interview (Stage 3) -------------------------------------------


def build_interview_scenario(application_id: int) -> dict[str, Any] | None:
    """Build a single Talk Buddy scenario for the hiring interview.

    Reuses the live interview's system prompt so the practice run uses
    the exact same persona/instructions the real session will use. The
    student's resume context is folded in if it exists, so practising
    after a strong resume is meaningfully different from practising
    after a weak one.

    Returns None if the application or job can't be resolved.
    """
    app_data = get_application(application_id)
    if not app_data:
        return None

    company_slug = app_data["company_slug"]
    job_slug = app_data["job_slug"]
    job = get_job(company_slug, job_slug) or {}

    manager_persona = job.get("manager_persona", "")
    manager_name = job.get("reports_to", "Hiring Manager")
    company_name = job.get("company", company_slug)
    job_title = app_data.get("job_title", job_slug)
    job_description = job.get("description", "")

    # Pull in resume context if the student has already submitted one
    resume_score: int | None = None
    resume_strengths: list[str] = []
    resume_gaps: list[str] = []
    cover_letter: str | None = None
    resume_results = get_stage_results(application_id, "resume")
    if resume_results:
        latest = resume_results[-1]
        resume_score = latest.get("score")
        feedback = latest.get("feedback") or {}
        resume_strengths = feedback.get("strengths", [])
        resume_gaps = feedback.get("gaps", [])

    system_prompt = build_interview_system_prompt(
        manager_persona=manager_persona,
        job_title=job_title,
        company_name=company_name,
        job_description=job_description,
        resume_score=resume_score,
        resume_strengths=resume_strengths,
        resume_gaps=resume_gaps,
        cover_letter=cover_letter,
    )

    initial_message = (
        f"Hi, thanks for making time today. Before we dive in, "
        f"could you tell me a bit about yourself and what drew you to "
        f"the {job_title} role at {company_name}?"
    )

    now = _now_iso()
    return {
        "id": _scenario_id("interview", company_slug, job_slug),
        "name": f"Practice: {job_title} interview at {company_name}",
        "description": (
            f"Rehearse the hiring interview for {job_title} at "
            f"{company_name}. Same hiring manager persona as the live "
            f"WorkReady simulation — but a safe space to fumble first."
        ),
        "category": "Business",
        "difficulty": "intermediate",
        "estimatedMinutes": 12,
        "systemPrompt": system_prompt,
        "initialMessage": initial_message,
        "tags": [
            "workready",
            "interview",
            "hiring",
            company_slug,
            job_slug,
        ],
        "isPublic": False,
        "created": now,
        "updated": now,
    }


def export_interview_package(application_id: int) -> dict[str, Any] | None:
    """Build the full Talk Buddy package payload for a hiring interview."""
    scenario = build_interview_scenario(application_id)
    if not scenario:
        return None
    app_data = get_application(application_id) or {}
    job = get_job(app_data.get("company_slug", ""), app_data.get("job_slug", "")) or {}
    company_name = job.get("company", app_data.get("company_slug", ""))
    job_title = app_data.get("job_title", "")
    return wrap_as_package(
        [scenario],
        title=f"WorkReady — {job_title} interview prep",
        description=(
            f"Hiring interview rehearsal for {job_title} at {company_name}. "
            f"Generated from the WorkReady simulation."
        ),
    )


# --- Lunchroom (Stage 5) --------------------------------------------------


def build_lunchroom_scenarios(session_id: int) -> list[dict[str, Any]]:
    """Build one Talk Buddy scenario per AI participant in a lunchroom session.

    Each scenario reuses the persona prompt from
    `content/employees/{slug}-prompt.txt` (via `lunchroom_chat._load_persona`)
    and frames the conversation as a 1:1 lunch chat — appropriate for
    rehearsing small talk with each colleague before the real group
    lunch fires.

    Returns an empty list if the session can't be resolved.
    """
    session = get_lunchroom_session(session_id)
    if not session:
        return []

    participants = session.get("participants") or []
    if not participants:
        return []

    app_data = get_application(session["application_id"]) or {}
    company_slug = app_data.get("company_slug", "")
    job = get_job(company_slug, app_data.get("job_slug", "")) or {}
    company_name = (
        job.get("company")
        or (get_company(company_slug) or {}).get("company")
        or company_slug
    )

    occasion = session.get("occasion") or "routine_lunch"
    occasion_detail = session.get("occasion_detail") or ""

    scenarios: list[dict[str, Any]] = []
    now = _now_iso()
    for p in participants:
        slug = p.get("slug", "")
        name = p.get("name", slug)
        role = p.get("role", "")

        persona = lunchroom_chat._load_persona(company_slug, slug) if slug else ""

        occasion_blurb = (
            f"\n\nOccasion: {occasion}"
            + (f" — {occasion_detail}" if occasion_detail else "")
        )

        system_prompt = (
            f"{persona}\n\n"
            f"═══════════════════════════════════════════════════\n"
            f"You are {name}, " + (f"{role} at " if role else "at ")
            + f"{company_name}. You're having a casual one-on-one lunch "
            f"with a new intern who's just joined the team. The intern "
            f"is using this chat to practise informal workplace small "
            f"talk before the real group lunch.\n"
            f"{occasion_blurb}\n\n"
            f"Stay fully in character as {name}. Be warm, professional, "
            f"and human. Speak in first person. Ask the intern about "
            f"themselves, their background, what they're enjoying so "
            f"far. Share small things about your own work and the "
            f"company in a natural way. Keep messages short — one or "
            f"two sentences usually, the way you'd actually talk over "
            f"lunch. Do not break character. Do not mention you're an "
            f"AI or that this is a simulation."
        )

        first_name = name.split()[0] if name else "Hi"
        initial_message = (
            f"Hey, grab a seat — I'm {first_name}. Glad you could make "
            f"it along. How are you settling in so far?"
        )

        scenarios.append({
            "id": _scenario_id("lunchroom", company_slug, slug, session_id),
            "name": f"Practice: lunch with {name}",
            "description": (
                f"Rehearse 1:1 small talk with {name}"
                + (f" ({role})" if role else "")
                + f" at {company_name}. Use this before the real "
                f"WorkReady lunchroom chat — same persona, low-stakes."
            ),
            "category": "Social",
            "difficulty": "beginner",
            "estimatedMinutes": 8,
            "systemPrompt": system_prompt,
            "initialMessage": initial_message,
            "tags": [
                "workready",
                "lunchroom",
                "small-talk",
                company_slug,
                slug,
            ],
            "isPublic": False,
            "created": now,
            "updated": now,
        })

    return scenarios


def export_lunchroom_package(session_id: int) -> dict[str, Any] | None:
    """Build the full Talk Buddy package payload for a lunchroom session."""
    scenarios = build_lunchroom_scenarios(session_id)
    if not scenarios:
        return None
    session = get_lunchroom_session(session_id) or {}
    app_data = get_application(session.get("application_id", 0)) or {}
    job = get_job(app_data.get("company_slug", ""), app_data.get("job_slug", "")) or {}
    company_name = job.get("company", app_data.get("company_slug", ""))
    return wrap_as_package(
        scenarios,
        title=f"WorkReady — Lunchroom prep at {company_name}",
        description=(
            f"Practise small talk 1:1 with each of your future lunchroom "
            f"colleagues at {company_name} before the real chat opens."
        ),
    )

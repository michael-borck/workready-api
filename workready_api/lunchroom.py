"""Stage 5 — Lunchroom moment: invitation and session coordination.

This module handles the async invitation flow for Stage 5a:

- Pick an occasion (routine lunch, birthday, task celebration, etc.)
- Pick 2-4 company colleagues as participants (mentor excluded by default)
- Generate 3 lunchtime slots the student can pick from, respecting the
  company's business hours
- Drop a warm, casual invitation message in the student's work inbox
  carrying structured slot data the portal renders as buttons
- Transition the session through invited → accepted | declined | missed

Stage 5b (the chat scheduler) and Stage 5c (end-of-session notes and
supportive system feedback) are not in this module yet.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from typing import Any

from workready_api import scheduling
from workready_api.db import (
    count_lunchroom_outcomes,
    create_calendar_event,
    create_lunchroom_invitation,
    create_message,
    decline_lunchroom_invitation,
    get_application,
    get_db,
    get_lunchroom_session,
    get_student_by_id,
    pick_lunchroom_slot,
)
from workready_api.jobs import get_company, get_job


# --- Occasion selection ---------------------------------------------------

# Weighted default occasion pool. Each entry: (occasion_key, weight).
DEFAULT_OCCASIONS: list[tuple[str, int]] = [
    ("routine_lunch", 40),
    ("task_celebration", 25),
    ("birthday", 10),
    ("staff_award", 10),
    ("project_launch", 10),
    ("cultural_event", 5),
]

OCCASION_LABELS: dict[str, str] = {
    "routine_lunch": "regular team lunch",
    "task_celebration": "team lunch — celebrating recent work",
    "birthday": "team lunch — colleague's birthday",
    "staff_award": "team lunch — staff recognition",
    "project_launch": "team lunch — project milestone",
    "cultural_event": "team lunch — cultural event",
}


def _resolved_occasion_pool() -> list[tuple[str, int]]:
    """Apply any LUNCHROOM_OCCASIONS env allow-list to the default pool."""
    raw = (scheduling.LUNCHROOM_OCCASIONS or "").strip()
    if not raw:
        return DEFAULT_OCCASIONS
    allowed = {o.strip() for o in raw.split(",") if o.strip()}
    filtered = [(k, w) for (k, w) in DEFAULT_OCCASIONS if k in allowed]
    return filtered or DEFAULT_OCCASIONS


def pick_occasion(
    trigger_source: str = "task_review",
    trigger_task: dict[str, Any] | None = None,
) -> tuple[str, str | None]:
    """Pick an occasion for a new lunchroom session.

    Weighted random from the configured pool. If the trigger is a task
    review AND 'task_celebration' is in the pool, give it a small nudge
    (the lunch right after a task submission plausibly celebrates it).

    Returns (occasion_key, occasion_detail). occasion_detail is a short
    free-text hint like "Sarah's birthday" or "wrapping up the quarterly
    report" — fed into the LLM system prompts in Stage 5b so colleagues
    can reference it naturally.
    """
    pool = _resolved_occasion_pool()

    # Nudge: if this invitation was triggered by a task review and the
    # student's just submitted work, task_celebration gets a bonus weight.
    if trigger_source == "task_review":
        pool = [
            (k, w * 2 if k == "task_celebration" else w) for (k, w) in pool
        ]

    keys = [k for k, _ in pool]
    weights = [w for _, w in pool]
    occasion = random.choices(keys, weights=weights, k=1)[0]  # noqa: S311

    detail = _build_occasion_detail(occasion, trigger_task)
    return occasion, detail


def _build_occasion_detail(
    occasion: str, trigger_task: dict[str, Any] | None,
) -> str | None:
    """Build a short human-readable detail string for the occasion.

    Kept intentionally lightweight — the real texture comes from the
    LLM calls in Stage 5b which will be seeded with this hint.
    """
    if occasion == "task_celebration" and trigger_task:
        title = trigger_task.get("title", "the recent task")
        return f"wrapping up work on: {title}"
    if occasion == "birthday":
        # Fictional colleague name — Stage 5b's prompt can tell the LLM
        # the birthday person doesn't need to be in the participants
        # list; it's just ambient context.
        return random.choice(  # noqa: S311
            ["Sarah in accounts", "Liam in ops", "Priya in comms",
             "Marcus from the last intake", "Elena from the field team"],
        )
    if occasion == "staff_award":
        return random.choice(  # noqa: S311
            ["regional service award", "quarterly recognition",
             "long-service milestone"],
        )
    if occasion == "project_launch":
        return random.choice(  # noqa: S311
            ["the new client portal going live",
             "the field report making it out on time",
             "closing out a tricky piece of work"],
        )
    if occasion == "cultural_event":
        # Keep these respectful and additive — the LLM prompt in 5b
        # will be told to acknowledge but not centre the event unless a
        # participant persona naturally would.
        return random.choice(  # noqa: S311
            ["NAIDOC Week", "Diwali", "Lunar New Year",
             "Ramadan iftar week", "Harmony Week"],
        )
    return None


# --- Participant selection ------------------------------------------------


def pick_participants(
    company_slug: str,
    mentor_slug: str | None,
    count: int | None = None,
    include_mentor: bool | None = None,
) -> list[dict[str, Any]]:
    """Pick N employees from the company as lunchroom participants.

    Excludes the mentor by default. Falls back to fewer participants if
    the company roster is too small to meet `count`.
    """
    target = count if count is not None else scheduling.LUNCHROOM_PARTICIPANT_COUNT
    include = (
        include_mentor
        if include_mentor is not None
        else scheduling.LUNCHROOM_INCLUDE_MENTOR
    )

    company = get_company(company_slug) or {}
    employees = list(company.get("employees", []) or [])
    if not employees:
        # jobs.json may not carry employees — fall back to reports_to
        # names from job listings as a minimal cast.
        employees = _fallback_employees_from_jobs(company_slug)

    # Filter out the mentor unless include_mentor is true
    pool: list[dict[str, Any]] = []
    for emp in employees:
        slug = emp.get("slug") or _slugify_name(emp.get("name", ""))
        if not include and mentor_slug and slug == mentor_slug:
            continue
        pool.append({
            "slug": slug,
            "name": emp.get("name", slug),
            "role": emp.get("role") or emp.get("title", ""),
        })

    if not pool:
        return []

    random.shuffle(pool)  # noqa: S311
    return pool[:max(1, min(target, len(pool)))]


def _slugify_name(name: str) -> str:
    import re
    s = re.sub(r"[^\w\s-]", "", name.lower())
    s = re.sub(r"\s+", "-", s).strip("-")
    return s or "colleague"


def _fallback_employees_from_jobs(company_slug: str) -> list[dict[str, Any]]:
    """Seed a minimal employee pool from the reports_to names in jobs.json.

    Used when the company's jobs.json doesn't carry a full employees list.
    Deduplicates by name.
    """
    from workready_api.jobs import _JOB_CACHE  # type: ignore[attr-defined]
    seen: dict[str, dict[str, Any]] = {}
    for (slug_key, _job_slug), job in _JOB_CACHE.items():
        if slug_key != company_slug:
            continue
        reports_to = job.get("reports_to", "")
        if not reports_to or reports_to in seen:
            continue
        seen[reports_to] = {
            "slug": _slugify_name(reports_to),
            "name": reports_to,
            "role": job.get("department", "Team member"),
        }
    return list(seen.values())


# --- Slot generation ------------------------------------------------------


def generate_lunchroom_slots(company_slug: str) -> list[str]:
    """Produce LUNCHROOM_SLOTS_OFFERED lunchtime slots for the student.

    Reuses scheduling.generate_slots but narrows the preference to
    lunch hours (LUNCHROOM_TIME_OF_DAY_START..END in local time) and
    applies the lead/horizon window.
    """
    bh_raw = get_company(company_slug) or {}
    business_hours = (bh_raw.get("business_hours") or {}) if bh_raw else {}
    bh_config = scheduling.BusinessHoursConfig.from_dict(business_hours or None)

    # Custom config clipping: narrow the working-day window down to
    # the lunchroom time-of-day. If the company's business hours don't
    # overlap with the lunch window, fall back to the intersection or
    # the narrowest sensible window.
    lunch_start = max(bh_config.start, scheduling.LUNCHROOM_TIME_OF_DAY_START)
    lunch_end = min(bh_config.end, scheduling.LUNCHROOM_TIME_OF_DAY_END)
    if lunch_start >= lunch_end:
        # Company's hours don't overlap lunch — use the configured
        # lunch window directly and let the slot generator see if any
        # slots land on valid days.
        lunch_start = scheduling.LUNCHROOM_TIME_OF_DAY_START
        lunch_end = scheduling.LUNCHROOM_TIME_OF_DAY_END

    lunch_config = scheduling.BusinessHoursConfig(
        start=lunch_start,
        end=lunch_end,
        days=list(bh_config.days),
        description=f"Lunchroom window ({lunch_start}:00–{lunch_end}:00)",
    )

    prefs = scheduling.SlotPreferences(days=list(lunch_config.days), time_of_day="any")

    after = scheduling.now_utc() + timedelta(
        hours=scheduling.LUNCHROOM_INVITE_LEAD_HOURS,
    )
    slots = scheduling.generate_slots(
        prefs,
        after=after,
        count=scheduling.LUNCHROOM_SLOTS_OFFERED,
        excluded=set(),
        config=lunch_config,
    )
    # Clip to the horizon window (generate_slots also respects its own
    # LATEST_BOOKING_DAYS but that's 14 by default — we want tighter)
    latest = scheduling.now_utc() + timedelta(
        days=scheduling.LUNCHROOM_INVITE_HORIZON_DAYS,
    )
    slots = [s for s in slots if s <= latest]
    return [scheduling.to_iso(s) for s in slots]


# --- Invitation creation --------------------------------------------------


def create_invitation(
    application: dict[str, Any],
    trigger_source: str = "task_review",
    trigger_task: dict[str, Any] | None = None,
) -> int | None:
    """Create a lunchroom invitation for an application.

    Returns the new lunchroom_session id, or None if:
      - the invite cap has been reached
      - no colleagues could be picked
      - no valid lunch slots could be generated

    Side effects:
      - Inserts a lunchroom_sessions row in 'invited' state
      - Drops a warm casual invitation message in the student's work inbox,
        scheduled to land a few minutes after the triggering feedback

    The returned session ID is carried on the message so the portal can
    render the pick-slot buttons.
    """
    application_id = application["id"]

    # Respect the total invite cap
    total = count_lunchroom_outcomes(
        application_id,
        statuses=["invited", "accepted", "active", "completed",
                  "declined", "missed"],
    )
    if total >= scheduling.LUNCHROOM_INVITES:
        return None

    # Resolve company + mentor
    job = get_job(application["company_slug"], application["job_slug"]) or {}
    company_name = job.get("company", application["company_slug"])
    mentor_name = job.get("reports_to", "")
    mentor_slug = _slugify_name(mentor_name) if mentor_name else None

    participants = pick_participants(
        company_slug=application["company_slug"],
        mentor_slug=mentor_slug,
    )
    if not participants:
        return None

    slots = generate_lunchroom_slots(application["company_slug"])
    if not slots:
        return None

    occasion, occasion_detail = pick_occasion(
        trigger_source=trigger_source, trigger_task=trigger_task,
    )

    session_id = create_lunchroom_invitation(
        application_id=application_id,
        occasion=occasion,
        occasion_detail=occasion_detail,
        participants=participants,
        proposed_slots=slots,
        trigger_source=trigger_source,
        trigger_task_id=(trigger_task or {}).get("id"),
    )

    # Compose the inbox invitation message
    student = get_student_by_id(application["student_id"]) or {}
    first_name = (student.get("name") or "").split()[0] if student.get("name") else "there"
    inviter = participants[0]  # use the first picked colleague as the inviter
    inviter_name = inviter["name"]
    slot_lines = "\n".join(
        f"  • {_format_slot_human(s)}" for s in slots
    )
    occasion_blurb = _occasion_blurb(occasion, occasion_detail)

    body = (
        f"Hey {first_name},\n\n"
        f"{occasion_blurb} A few of us are heading out for lunch and "
        f"it'd be great if you could join — good chance to get to know "
        f"the team outside of work stuff. Here are the slots that work "
        f"for us:\n\n"
        f"{slot_lines}\n\n"
        f"Just pick whichever one suits, or let me know if you can't "
        f"make it this round — no worries either way, there'll be "
        f"others.\n\n"
        f"— {inviter_name}"
    )

    # Invitation lands shortly after the triggering feedback — we use
    # a small delay so it doesn't crowd the feedback email. If the
    # triggering task's feedback was delayed, schedule relative to now.
    deliver_at = scheduling.feedback_delivery_time(2, 3)  # ~2 min ± 3

    msg_id = create_message(
        student_id=student["id"],
        student_email=student.get("email", application.get("student_email", "")),
        sender_name=inviter_name,
        sender_role=f"{inviter['role']} at {company_name}" if inviter.get("role") else company_name,
        subject=f"Lunch this week? — {company_name}",
        body=body,
        inbox="work",
        application_id=application_id,
        related_stage="lunchroom",
        deliver_at=deliver_at,
    )

    # Attach the message ID to the session so the portal can find it later
    with get_db() as conn:
        conn.execute(
            "UPDATE lunchroom_sessions SET invitation_message_id = ? WHERE id = ?",
            (msg_id, session_id),
        )

    return session_id


def _occasion_blurb(occasion: str, detail: str | None) -> str:
    """Short context sentence that opens the invitation, tailored to the occasion."""
    if occasion == "task_celebration":
        if detail:
            return f"Great job on {detail} — we thought that deserves a proper lunch."
        return "Nice work this week — we thought that deserves a proper lunch."
    if occasion == "birthday":
        if detail:
            return f"It's {detail}'s birthday and we're marking it with a team lunch."
        return "It's a birthday — we're marking it with a team lunch."
    if occasion == "staff_award":
        if detail:
            return f"Quick team lunch to mark the {detail} — worth acknowledging."
        return "Quick team lunch to mark a bit of team recognition — worth acknowledging."
    if occasion == "project_launch":
        if detail:
            return f"Small celebration — {detail}."
        return "Small celebration — a milestone we wanted to mark together."
    if occasion == "cultural_event":
        if detail:
            return f"A few of us are marking {detail} with a team lunch — everyone welcome."
        return "A few of us are doing a team lunch — everyone welcome."
    return "Regular team lunch this week."


def _format_slot_human(iso: str) -> str:
    """Convert a UTC ISO slot to a human-friendly local display."""
    try:
        dt = scheduling.from_iso(iso)
    except Exception:  # noqa: BLE001
        return iso
    local = scheduling.to_local(dt)
    # e.g. "Wed 16 Apr, 12:30 PM"
    return local.strftime("%a %d %b, %-I:%M %p")


# --- Slot picking / declining ---------------------------------------------


def accept_slot(session_id: int, scheduled_at_iso: str) -> dict[str, Any] | None:
    """Student picks one of the proposed slots.

    Validates the slot is one of the proposed options, transitions the
    session to 'accepted', and materialises a calendar_events row so
    the calendar view shows the upcoming lunch.
    """
    session = get_lunchroom_session(session_id)
    if not session:
        return None
    if session["status"] != "invited":
        return session  # idempotent — re-picking is a no-op
    if scheduled_at_iso not in (session.get("proposed_slots") or []):
        return None

    # Materialise the calendar event first so we can attach its id
    # to the session row.
    participants_preview = ", ".join(
        p.get("name", "") for p in (session.get("participants") or [])[:3]
    )
    title = "Team lunch"
    description = (
        f"With: {participants_preview}" if participants_preview
        else "Company team lunch"
    )
    event_id = create_calendar_event(
        application_id=session["application_id"],
        event_type="lunchroom",
        title=title,
        description=description,
        scheduled_at=scheduled_at_iso,
        related_id=session_id,
        status="upcoming",
    )

    pick_lunchroom_slot(
        session_id=session_id,
        scheduled_at=scheduled_at_iso,
        calendar_event_id=event_id,
    )
    return get_lunchroom_session(session_id)


def decline(session_id: int) -> dict[str, Any] | None:
    """Student declines the invitation."""
    session = get_lunchroom_session(session_id)
    if not session:
        return None
    if session["status"] != "invited":
        return session  # idempotent
    decline_lunchroom_invitation(session_id)
    return get_lunchroom_session(session_id)


# --- Mentor check-in on decline threshold ---------------------------------


def maybe_send_decline_check_in(application_id: int) -> bool:
    """If declines+misses have reached LUNCHROOM_DECLINE_LIMIT, send a
    gentle mentor check-in email (once). Returns True if a message was sent.
    """
    negative = count_lunchroom_outcomes(
        application_id, statuses=["declined", "missed"],
    )
    if negative < scheduling.LUNCHROOM_DECLINE_LIMIT:
        return False

    # Only send once — use a flag on the application. For v1 we detect
    # "already sent" by checking for an existing related_stage='lunchroom'
    # message from the mentor with a specific subject marker.
    app_data = get_application(application_id)
    if not app_data:
        return False

    marker_subject = "Everything okay? (lunch invites)"
    with get_db() as conn:
        row = conn.execute(
            "SELECT 1 FROM messages WHERE application_id = ? "
            "AND related_stage = 'lunchroom' AND subject = ?",
            (application_id, marker_subject),
        ).fetchone()
    if row:
        return False  # already sent

    job = get_job(app_data["company_slug"], app_data["job_slug"]) or {}
    company_name = job.get("company", app_data["company_slug"])
    mentor_name = job.get("reports_to", "Your mentor")
    student = get_student_by_id(app_data["student_id"]) or {}
    first_name = (student.get("name") or "").split()[0] if student.get("name") else "there"

    body = (
        f"Hey {first_name},\n\n"
        f"Quick one — I noticed you've had to pass on a couple of the "
        f"team lunches. No judgement at all, I know everyone's busy "
        f"and these things don't always fit. Just wanted to check in: "
        f"is everything going okay? If there's anything making it hard "
        f"to join those informal catch-ups — workload, timing, anything "
        f"at all — let me know and we can figure something out. And if "
        f"you'd rather just focus on the work for now, that's completely "
        f"fine too.\n\n"
        f"Either way, the team likes having you around.\n\n"
        f"— {mentor_name}"
    )
    create_message(
        student_id=student.get("id") or 0,
        student_email=student.get("email") or app_data.get("student_email", ""),
        sender_name=mentor_name,
        sender_role=f"Your mentor at {company_name}",
        subject=marker_subject,
        body=body,
        inbox="work",
        application_id=application_id,
        related_stage="lunchroom",
    )
    return True

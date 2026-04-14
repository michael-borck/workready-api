"""Business hours, presence, and public holiday helpers.

Pure functions. No DB writes. Used by the team directory (to compute
presence_ok per team member) and by the chat and mail reply paths (to
compute deliver_at that looks like office hours).

Three concepts:

- **Business hours** — configured per company in brief.yaml
  (business_hours.start, .end, .days). Defaults to 9-5 Mon-Fri if a
  company's brief doesn't set them.
- **Presence** — per-character, optional field on brief.yaml
  employees[].availability. Status: available | away | travelling |
  sick | on_leave. Default: available.
- **Public holidays** — optional per-company reference to a region
  in data/public_holidays.py via brief.yaml business_hours.holidays_region.
  Default: no filtering.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from workready_api.data.public_holidays import holiday_dates_for_region
from workready_api.jobs import get_company


LOCAL_TZ = ZoneInfo("Australia/Perth")


def _company_config(company_slug: str) -> dict:
    """Return the company's business_hours dict with sensible defaults."""
    company = get_company(company_slug) or {}
    bh = company.get("business_hours", {}) or {}
    return {
        "start": int(bh.get("start", 9)),
        "end": int(bh.get("end", 17)),
        "days": list(bh.get("days", [1, 2, 3, 4, 5])),  # Mon-Fri
        "holidays_region": bh.get("holidays_region"),
    }


def _employees_roster(company_slug: str) -> list[dict]:
    """Return the employees roster from jobs.json (passthrough from brief.yaml)."""
    company = get_company(company_slug) or {}
    return company.get("employees", []) or []


def _now_local() -> datetime:
    return datetime.now(timezone.utc).astimezone(LOCAL_TZ)


def is_public_holiday(company_slug: str, date_local: datetime | None = None) -> bool:
    """True if the given date (in company-local time) is a public holiday
    for this company's configured region. Unknown region → always False.
    """
    cfg = _company_config(company_slug)
    region = cfg.get("holidays_region")
    if not region:
        return False
    if date_local is None:
        date_local = _now_local()
    iso_date = date_local.strftime("%Y-%m-%d")
    return iso_date in holiday_dates_for_region(region)


def _is_within_business_hours(company_slug: str, dt_local: datetime) -> bool:
    """True if dt_local falls inside this company's business hours AND
    is on a business day AND is not a public holiday.
    """
    cfg = _company_config(company_slug)
    weekday = dt_local.isoweekday()  # 1=Mon..7=Sun
    if weekday not in cfg["days"]:
        return False
    hour = dt_local.hour
    if not (cfg["start"] <= hour < cfg["end"]):
        return False
    if is_public_holiday(company_slug, dt_local):
        return False
    return True


def is_character_available(company_slug: str, character_slug: str) -> bool:
    """True if a team member is contactable right now.

    Checks (in order):
    1. Business hours + business day
    2. Public holiday
    3. Character presence state (availability.status)
    """
    now_local = _now_local()
    if not _is_within_business_hours(company_slug, now_local):
        return False

    for emp in _employees_roster(company_slug):
        if emp.get("slug") != character_slug:
            continue
        avail = emp.get("availability") or {}
        status = avail.get("status", "available")
        if status != "available":
            # Check return_date — if in the past, treat as available
            return_date = avail.get("return_date")
            if return_date:
                try:
                    rd = datetime.fromisoformat(return_date).replace(tzinfo=LOCAL_TZ)
                    if now_local >= rd:
                        return True
                except (ValueError, TypeError):
                    pass
            return False
        return True

    # Character not in roster → treat as available but log noise
    import logging
    logging.getLogger(__name__).warning(
        "is_character_available: character %s not in roster for %s",
        character_slug, company_slug,
    )
    return True


def next_business_hours_slot(
    company_slug: str,
    after_utc: datetime | None = None,
    *,
    jitter_minutes: int = 30,
    runaway_guard_days: int = 30,
) -> str:
    """Compute the next timestamp (as UTC ISO string) that:

    - Is on a business day for this company
    - Is inside business hours (company-local)
    - Is NOT a public holiday

    If `after_utc` is already inside business hours, returns that time
    plus a small random jitter (0..jitter_minutes) to simulate realistic
    reply latency. Otherwise advances to the next valid slot (next
    business day start + jitter).

    The runaway guard caps the search at `runaway_guard_days` — if no
    valid slot is found within that window, raises RuntimeError (should
    never happen in practice).
    """
    import random

    if after_utc is None:
        after_utc = datetime.now(timezone.utc)

    cfg = _company_config(company_slug)
    probe_local = after_utc.astimezone(LOCAL_TZ)

    # If already in business hours, small jitter and return
    if _is_within_business_hours(company_slug, probe_local):
        jitter = random.randint(0, max(jitter_minutes, 1))  # noqa: S311
        result_local = probe_local + timedelta(minutes=jitter)
        if _is_within_business_hours(company_slug, result_local):
            return result_local.astimezone(timezone.utc).isoformat()

    # Otherwise walk forward to the next valid business-day start
    probe_local = probe_local.replace(
        hour=cfg["start"], minute=0, second=0, microsecond=0,
    )
    if probe_local <= after_utc.astimezone(LOCAL_TZ):
        probe_local += timedelta(days=1)

    for _ in range(runaway_guard_days):
        weekday = probe_local.isoweekday()
        if weekday in cfg["days"] and not is_public_holiday(company_slug, probe_local):
            jitter = random.randint(0, max(jitter_minutes, 1))  # noqa: S311
            result_local = probe_local + timedelta(minutes=jitter)
            return result_local.astimezone(timezone.utc).isoformat()
        probe_local += timedelta(days=1)

    raise RuntimeError(
        f"next_business_hours_slot: no valid slot found within "
        f"{runaway_guard_days} days for {company_slug}"
    )


def compute_reply_deliver_at(
    company_slug: str,
    student_last_login_iso: str | None,
    *,
    now_utc: datetime | None = None,
) -> str:
    """Business-hours deliver_at with absent-student backdating.

    If the student has been active recently (< 24h ago), use
    next_business_hours_slot for realistic office-hours delivery.

    If the student has been absent > 24h, backdate the reply to a
    plausible business-hours slot BEFORE now, so the student returns to
    find the reply "already in their inbox from earlier today".
    """
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)

    def _walk_backwards_to_business_hours(anchor: datetime) -> datetime:
        """From `anchor`, walk backwards to the most recent plausible
        business-hours moment and return that as UTC."""
        cfg = _company_config(company_slug)
        probe_local = anchor.astimezone(LOCAL_TZ)
        for _ in range(30):
            if _is_within_business_hours(company_slug, probe_local):
                return probe_local.astimezone(timezone.utc)
            probe_local -= timedelta(hours=1)
        # Fallback: earlier today 14:00 local
        return anchor.astimezone(LOCAL_TZ).replace(
            hour=14, minute=0, second=0, microsecond=0,
        ).astimezone(timezone.utc)

    if student_last_login_iso is None:
        return next_business_hours_slot(company_slug, now_utc)

    try:
        last_login = datetime.fromisoformat(student_last_login_iso)
        if last_login.tzinfo is None:
            last_login = last_login.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return next_business_hours_slot(company_slug, now_utc)

    hours_absent = (now_utc - last_login).total_seconds() / 3600
    if hours_absent <= 24:
        return next_business_hours_slot(company_slug, now_utc)

    # Absent > 24h: backdate to plausible office hours earlier today
    backdated = _walk_backwards_to_business_hours(now_utc)
    return backdated.isoformat()

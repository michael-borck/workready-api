"""Scheduling, business hours, and slot generation for interview booking.

All datetimes are stored in UTC. Display and business-hours checks happen
in the configured local timezone (default Australia/Perth).

Configuration via env vars (with sensible defaults):

INTERVIEW_BOOKING_ENABLED=false      Default off so dev/testing isn't blocked
BUSINESS_HOURS_START=9               24-hour, local time
BUSINESS_HOURS_END=17
BUSINESS_DAYS=1,2,3,4,5              Mon-Fri (1=Mon, 7=Sun)
TIMEZONE=Australia/Perth
SLOT_DURATION_MINUTES=30
SLOTS_OFFERED=4                      Slots per preference query
LATE_GRACE_MINUTES=5
MAX_MISSED_INTERVIEWS=3
EARLIEST_BOOKING_HOURS=2             Don't offer slots within N hours of now
LATEST_BOOKING_DAYS=14               Don't offer slots beyond N days out
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo


def _env_bool(name: str, default: bool) -> bool:
    val = os.environ.get(name, "").lower()
    if val in ("1", "true", "yes", "on"):
        return True
    if val in ("0", "false", "no", "off"):
        return False
    return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (ValueError, TypeError):
        return default


def _env_int_list(name: str, default: list[int]) -> list[int]:
    raw = os.environ.get(name, "")
    if not raw:
        return default
    try:
        return [int(x.strip()) for x in raw.split(",") if x.strip()]
    except ValueError:
        return default


# --- Configuration (resolved at import time so env changes need restart) ---


def _resolve_timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001
        return ZoneInfo("Australia/Perth")


BOOKING_ENABLED: bool = _env_bool("INTERVIEW_BOOKING_ENABLED", False)
BUSINESS_HOURS_START: int = _env_int("BUSINESS_HOURS_START", 9)
BUSINESS_HOURS_END: int = _env_int("BUSINESS_HOURS_END", 17)
BUSINESS_DAYS: list[int] = _env_int_list("BUSINESS_DAYS", [1, 2, 3, 4, 5])
TIMEZONE_NAME: str = os.environ.get("TIMEZONE", "Australia/Perth")
LOCAL_TZ: ZoneInfo = _resolve_timezone(TIMEZONE_NAME)
SLOT_DURATION_MINUTES: int = _env_int("SLOT_DURATION_MINUTES", 30)
SLOTS_OFFERED: int = _env_int("SLOTS_OFFERED", 4)
LATE_GRACE_MINUTES: int = _env_int("LATE_GRACE_MINUTES", 5)
MAX_MISSED_INTERVIEWS: int = _env_int("MAX_MISSED_INTERVIEWS", 3)
MAX_RESCHEDULES: int = _env_int("MAX_RESCHEDULES", 1)
RESCHEDULE_LIMIT_MODE: str = os.environ.get("RESCHEDULE_LIMIT_MODE", "hard").lower()
if RESCHEDULE_LIMIT_MODE not in ("hard", "soft"):
    RESCHEDULE_LIMIT_MODE = "hard"
EARLIEST_BOOKING_HOURS: int = _env_int("EARLIEST_BOOKING_HOURS", 2)
LATEST_BOOKING_DAYS: int = _env_int("LATEST_BOOKING_DAYS", 14)

RESUME_FEEDBACK_DELAY_MINUTES: int = _env_int("RESUME_FEEDBACK_DELAY_MINUTES", 0)
RESUME_FEEDBACK_DELAY_JITTER_MINUTES: int = _env_int(
    "RESUME_FEEDBACK_DELAY_JITTER_MINUTES", 0
)
INTERVIEW_INVITATION_DELAY_MINUTES: int = _env_int(
    "INTERVIEW_INVITATION_DELAY_MINUTES", 0
)


# --- Public holidays ---

# Default WA public holidays (and substitute days where applicable).
# Lecturers can override the entire list via the PUBLIC_HOLIDAYS env var
# (comma-separated YYYY-MM-DD strings) or extend by appending after parsing.
DEFAULT_PUBLIC_HOLIDAYS: list[str] = [
    # 2026
    "2026-01-01",  # New Year's Day (Thu)
    "2026-01-26",  # Australia Day (Mon)
    "2026-03-02",  # Labour Day (1st Mon March)
    "2026-04-03",  # Good Friday
    "2026-04-04",  # Easter Saturday
    "2026-04-05",  # Easter Sunday
    "2026-04-06",  # Easter Monday
    "2026-04-25",  # ANZAC Day (Sat)
    "2026-04-27",  # ANZAC Day substitute (Mon)
    "2026-06-01",  # WA Day (1st Mon June)
    "2026-09-28",  # King's Birthday WA (last Mon Sep)
    "2026-12-25",  # Christmas Day (Fri)
    "2026-12-26",  # Boxing Day (Sat)
    "2026-12-28",  # Boxing Day substitute (Mon)
    # 2027
    "2027-01-01",  # New Year's Day (Fri)
    "2027-01-26",  # Australia Day (Tue)
    "2027-03-01",  # Labour Day (1st Mon March)
    "2027-03-26",  # Good Friday
    "2027-03-27",  # Easter Saturday
    "2027-03-28",  # Easter Sunday
    "2027-03-29",  # Easter Monday
    "2027-04-25",  # ANZAC Day (Sun)
    "2027-04-26",  # ANZAC Day substitute (Mon)
    "2027-06-07",  # WA Day (1st Mon June)
    "2027-09-27",  # King's Birthday WA
    "2027-12-25",  # Christmas Day (Sat)
    "2027-12-27",  # Christmas Day substitute (Mon)
    "2027-12-28",  # Boxing Day substitute (Tue)
]


def _parse_holidays() -> set[str]:
    """Resolve the active public holiday list (env override or default)."""
    raw = os.environ.get("PUBLIC_HOLIDAYS", "").strip()
    if raw:
        return {d.strip() for d in raw.split(",") if d.strip()}
    return set(DEFAULT_PUBLIC_HOLIDAYS)


PUBLIC_HOLIDAYS: set[str] = _parse_holidays()


def is_public_holiday(dt: datetime) -> bool:
    """True if dt (local) falls on a configured public holiday."""
    local = to_local(dt)
    return local.strftime("%Y-%m-%d") in PUBLIC_HOLIDAYS


# --- Datetime helpers ---


def now_utc() -> datetime:
    """Current UTC time as a timezone-aware datetime."""
    return datetime.now(timezone.utc)


def now_local() -> datetime:
    """Current local (Perth) time."""
    return now_utc().astimezone(LOCAL_TZ)


def to_local(dt: datetime) -> datetime:
    """Convert any timezone-aware datetime to the local timezone."""
    return dt.astimezone(LOCAL_TZ)


def to_iso(dt: datetime) -> str:
    """Serialise to ISO 8601 string (UTC for storage)."""
    return dt.astimezone(timezone.utc).isoformat()


def from_iso(s: str) -> datetime:
    """Parse an ISO 8601 string into a timezone-aware datetime."""
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def add_jitter(delay_minutes: int, jitter_minutes: int) -> int:
    """Return delay_minutes ± jitter_minutes (uniform random)."""
    if jitter_minutes <= 0:
        return delay_minutes
    import random
    return delay_minutes + random.randint(-jitter_minutes, jitter_minutes)  # noqa: S311


def feedback_delivery_time(base_delay_minutes: int, jitter_minutes: int = 0) -> str:
    """Compute a deliver_at timestamp for a delayed feedback message.

    Returns an ISO string in UTC. If both delays are 0 returns now (instant).
    """
    delay = add_jitter(base_delay_minutes, jitter_minutes)
    if delay <= 0:
        return to_iso(now_utc())
    return to_iso(now_utc() + timedelta(minutes=delay))


# --- Business hours ---


def is_business_day(dt: datetime) -> bool:
    """True if dt (local) is a configured business day and not a public holiday.

    Mon=1 ... Sun=7
    """
    local = to_local(dt)
    iso_weekday = local.isoweekday()  # 1=Mon ... 7=Sun
    if iso_weekday not in BUSINESS_DAYS:
        return False
    if is_public_holiday(local):
        return False
    return True


def is_business_hour(dt: datetime) -> bool:
    """True if dt (local) is within configured business hours."""
    local = to_local(dt)
    return BUSINESS_HOURS_START <= local.hour < BUSINESS_HOURS_END


def is_business_time(dt: datetime) -> bool:
    """True if dt is both a business day and within business hours."""
    return is_business_day(dt) and is_business_hour(dt)


# --- Slot generation ---


TimeOfDay = Literal["morning", "afternoon", "any"]


@dataclass
class SlotPreferences:
    """Student's stated preferences for interview timing.

    days: ISO weekdays they're available (1=Mon ... 7=Sun). Empty = any.
    time_of_day: "morning" | "afternoon" | "any"
    """

    days: list[int]
    time_of_day: TimeOfDay = "any"

    @classmethod
    def from_query(cls, days: str | None, time_of_day: str | None) -> "SlotPreferences":
        """Parse from query string params."""
        day_list: list[int] = []
        if days:
            try:
                day_list = [int(d) for d in days.split(",") if d.strip()]
            except ValueError:
                day_list = []
        # Default to any business day if none specified
        if not day_list:
            day_list = list(BUSINESS_DAYS)

        tod: TimeOfDay = "any"
        if time_of_day in ("morning", "afternoon", "any"):
            tod = time_of_day  # type: ignore[assignment]
        return cls(days=day_list, time_of_day=tod)


def _matches_preferences(dt: datetime, prefs: SlotPreferences) -> bool:
    """Check if a slot time matches the student's preferences."""
    local = to_local(dt)
    if local.isoweekday() not in prefs.days:
        return False
    if prefs.time_of_day == "morning":
        # Morning = before 12pm local
        if local.hour >= 12:
            return False
    elif prefs.time_of_day == "afternoon":
        if local.hour < 12:
            return False
    return True


def _next_slot_boundary(after: datetime) -> datetime:
    """Round up `after` to the next slot boundary in local time."""
    local = to_local(after)
    minute = local.minute
    rounded_minute = ((minute // SLOT_DURATION_MINUTES) + 1) * SLOT_DURATION_MINUTES
    if rounded_minute >= 60:
        local = local.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    else:
        local = local.replace(minute=rounded_minute, second=0, microsecond=0)
    return local.astimezone(timezone.utc)


def generate_slots(
    prefs: SlotPreferences,
    *,
    after: datetime | None = None,
    count: int = SLOTS_OFFERED,
    excluded: set[str] | None = None,
) -> list[datetime]:
    """Generate `count` interview slots matching preferences.

    Args:
        prefs: student's day/time preferences
        after: don't offer slots before this UTC datetime (default: now + EARLIEST_BOOKING_HOURS)
        count: how many slots to return (max)
        excluded: ISO strings of slots to skip (already booked, already missed)

    Returns:
        List of UTC datetimes, in chronological order, that fit the preferences
        and lie within business hours and the booking window.
    """
    if excluded is None:
        excluded = set()

    if after is None:
        after = now_utc() + timedelta(hours=EARLIEST_BOOKING_HOURS)

    latest = now_utc() + timedelta(days=LATEST_BOOKING_DAYS)

    slots: list[datetime] = []
    cursor = _next_slot_boundary(after)
    safety_iterations = 0
    max_iterations = (LATEST_BOOKING_DAYS * 24 * 60) // SLOT_DURATION_MINUTES + 10

    while len(slots) < count and cursor <= latest and safety_iterations < max_iterations:
        safety_iterations += 1
        if (
            is_business_time(cursor)
            and _matches_preferences(cursor, prefs)
            and to_iso(cursor) not in excluded
        ):
            slots.append(cursor)
        cursor = cursor + timedelta(minutes=SLOT_DURATION_MINUTES)

    return slots


# --- Lateness checking ---


def can_start_now(scheduled_at: datetime) -> tuple[bool, str]:
    """Check whether the interview can start at this moment.

    Returns (allowed, reason). reason is "early", "late", or "ok".
    """
    now = now_utc()
    grace = timedelta(minutes=LATE_GRACE_MINUTES)
    earliest = scheduled_at - grace
    latest = scheduled_at + grace

    if now < earliest:
        return False, "early"
    if now > latest:
        return False, "late"
    return True, "ok"

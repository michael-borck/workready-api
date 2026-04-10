"""Notification adapter — single dispatch point for all student communications.

The API never directly calls "send a message via X". It calls notify(),
which decides which channels to use based on:
- The event type (some events have hard-coded channel requirements)
- The student's preferences (which channels they've opted into)
- The cohort's policy (configurable per cohort, future)

Adding a new channel = writing one adapter and registering it. The
calling code never changes.

MVP: in-app inbox only.
Phase 2: + email
Phase 3: + Telegram
Phase 4: + MS Teams
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from workready_api.db import create_message, get_student_by_email


# --- Event types ---

EventType = Literal[
    "welcome",
    "application_received",
    "interview_invitation",
    "application_rejected",
    "interview_passed",
    "interview_failed",
    "task_assigned",
    "task_feedback",
    "exit_interview_invitation",
    "internship_complete",
]


# --- Channel types ---

Channel = Literal["in_app", "email", "telegram", "teams", "sms"]


@dataclass
class NotifyContent:
    """Structured content for a notification.

    The notify() function formats this for each channel — in-app inbox
    stores subject + body, email might use HTML, Telegram uses markdown,
    SMS truncates to 160 chars, etc.

    deliver_at: optional UTC ISO string. If set, the notification is
    scheduled rather than delivered immediately. The in-app channel
    stores it on the message and the inbox endpoint hides it until
    the time arrives. Other channels may schedule via their own queue
    or fall back to immediate delivery.
    """

    sender_name: str
    sender_role: str = ""
    subject: str = ""
    body: str = ""
    application_id: int | None = None
    booking_id: int | None = None
    related_stage: str | None = None
    deliver_at: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


# --- Channel registry ---

ChannelHandler = Callable[[str, NotifyContent], None]
_REGISTRY: dict[Channel, ChannelHandler] = {}


def register_channel(channel: Channel, handler: ChannelHandler) -> None:
    """Register a channel adapter. Each channel has exactly one handler."""
    _REGISTRY[channel] = handler


def get_registered_channels() -> list[Channel]:
    """List all currently registered channels (for diagnostics)."""
    return list(_REGISTRY.keys())


# --- Routing rules ---

# Some events have mandatory channels (always send to in-app for record)
# and recommended channels (send if the student has opted in).
_EVENT_ROUTES: dict[EventType, list[Channel]] = {
    "welcome": ["in_app"],
    "application_received": ["in_app"],
    "interview_invitation": ["in_app"],
    "application_rejected": ["in_app"],
    "interview_passed": ["in_app"],
    "interview_failed": ["in_app"],
    "task_assigned": ["in_app"],
    "task_feedback": ["in_app"],
    "exit_interview_invitation": ["in_app"],
    "internship_complete": ["in_app"],
}


def _resolve_channels(
    event: EventType,
    student_email: str,
    requested: list[Channel] | str,
) -> list[Channel]:
    """Decide which channels to dispatch on for this event.

    "auto" → use the event's default routes filtered by registered channels
    explicit list → use the provided channels filtered by registered channels

    Future: also factor in student preferences and cohort policy.
    """
    if requested == "auto":
        candidates = _EVENT_ROUTES.get(event, ["in_app"])
    else:
        candidates = requested  # type: ignore[assignment]

    return [c for c in candidates if c in _REGISTRY]


# --- Public API ---


def notify(
    student_email: str,
    event: EventType,
    content: NotifyContent,
    channels: list[Channel] | str = "auto",
) -> None:
    """Send a notification to a student via one or more channels.

    Args:
        student_email: who to notify
        event: the event type (drives default routing)
        content: structured content (sender, subject, body, etc.)
        channels: "auto" (default) uses event routes, or pass an explicit list

    Example:
        notify(
            student_email="jane@curtin.edu.au",
            event="interview_invitation",
            content=NotifyContent(
                sender_name="NexusPoint Systems HR",
                sender_role="Recruitment Team",
                subject="Interview invitation — Junior Security Analyst",
                body="Dear Jane, ...",
                application_id=42,
                related_stage="interview",
            ),
        )
    """
    targets = _resolve_channels(event, student_email, channels)
    for channel in targets:
        handler = _REGISTRY[channel]
        try:
            handler(student_email, content)
        except Exception as exc:  # noqa: BLE001
            # Don't let one channel failure block the others
            import logging
            logging.getLogger(__name__).exception(
                "Notification channel %s failed for event %s: %s",
                channel, event, exc,
            )


# --- Built-in channel: in-app inbox ---


def _inapp_handler(student_email: str, content: NotifyContent) -> None:
    """Deliver to the student's personal in-app inbox."""
    student = get_student_by_email(student_email)
    if not student:
        # Should not happen — notify() is always called for known students
        import logging
        logging.getLogger(__name__).warning(
            "in_app handler: no student found for email %s", student_email,
        )
        return
    create_message(
        student_id=student["id"],
        student_email=student_email,
        sender_name=content.sender_name,
        sender_role=content.sender_role,
        subject=content.subject,
        body=content.body,
        inbox="personal",
        application_id=content.application_id,
        booking_id=content.booking_id,
        related_stage=content.related_stage,
        deliver_at=content.deliver_at,
    )


# Register the in-app channel at module load time
register_channel("in_app", _inapp_handler)

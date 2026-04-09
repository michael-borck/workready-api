"""Data models for the WorkReady simulation API."""

from __future__ import annotations

from pydantic import BaseModel


# --- Resume assessment ---


class FeedbackDetail(BaseModel):
    """Structured feedback on a resume submission."""

    strengths: list[str]
    gaps: list[str]
    suggestions: list[str]
    tailoring: str


class AssessmentResult(BaseModel):
    """Resume assessment response."""

    status: str = "reviewed"
    application_id: int | None = None
    fit_score: int  # 0-100
    feedback: FeedbackDetail
    proceed_to_interview: bool
    message: str = ""


# --- Simulation state ---


class ApplicationSummary(BaseModel):
    """Summary of a student's application."""

    id: int
    company_slug: str
    job_slug: str
    job_title: str
    source: str
    current_stage: str
    created_at: str
    updated_at: str


class StageResult(BaseModel):
    """Result of a single stage attempt."""

    id: int
    stage: str
    status: str
    score: int | None
    feedback: dict | None
    attempt: int
    created_at: str


class ApplicationDetail(BaseModel):
    """Full detail of an application including stage results."""

    application: ApplicationSummary
    stages: list[StageResult]


class StudentProgress(BaseModel):
    """All applications for a student."""

    email: str
    name: str
    applications: list[ApplicationSummary]


class StudentState(BaseModel):
    """High-level state for the portal — what the student should see."""

    email: str
    name: str
    state: str  # NOT_APPLIED, APPLIED, HIRED, COMPLETED
    active_application: ApplicationSummary | None = None
    applications: list[ApplicationSummary]
    unread_personal: int = 0
    unread_work: int = 0


class Message(BaseModel):
    """An inbox message."""

    id: int
    inbox: str
    sender_name: str
    sender_role: str | None = ""
    subject: str
    body: str
    application_id: int | None = None
    related_stage: str | None = None
    is_read: bool
    deliver_at: str
    created_at: str


class Inbox(BaseModel):
    """Inbox contents."""

    inbox: str
    messages: list[Message]
    unread_count: int

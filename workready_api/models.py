"""Data models for resume assessment."""

from __future__ import annotations

from pydantic import BaseModel


class ResumeSubmission(BaseModel):
    """Metadata submitted alongside the resume PDF."""

    company_slug: str
    job_slug: str
    job_title: str
    applicant_name: str
    applicant_email: str
    cover_letter: str = ""
    source: str = "direct"  # "direct" or "seek"


class FeedbackDetail(BaseModel):
    """Structured feedback on a resume submission."""

    strengths: list[str]
    gaps: list[str]
    suggestions: list[str]
    tailoring: str


class AssessmentResult(BaseModel):
    """Full assessment response."""

    status: str = "reviewed"
    fit_score: int  # 0-100
    feedback: FeedbackDetail
    proceed_to_interview: bool
    message: str = ""

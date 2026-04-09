"""Resume assessment logic.

Provides a structured assessment of a resume against a job posting.
Currently uses a rule-based stub. The `assess_with_llm` function is
ready for integration with a local LLM via the Ollama API.
"""

from __future__ import annotations

import json
import os
from typing import Any

from workready_api.models import AssessmentResult, FeedbackDetail


def assess_stub(
    resume_text: str,
    cover_letter: str,
    job_title: str,
    job_description: str,
) -> AssessmentResult:
    """Rule-based stub assessment for development and testing.

    Provides basic feedback based on text length and keyword matching.
    Replace with assess_with_llm() for production use.
    """
    resume_lower = resume_text.lower()
    job_lower = job_description.lower()

    # Simple keyword overlap scoring
    job_words = set(job_lower.split())
    resume_words = set(resume_lower.split())
    # Filter to meaningful words (>4 chars)
    job_keywords = {w for w in job_words if len(w) > 4}
    overlap = job_keywords & resume_words
    keyword_score = min(len(overlap) / max(len(job_keywords), 1) * 100, 100)

    # Length scoring
    length_score = min(len(resume_text) / 20, 30)  # up to 30 points for length

    # Cover letter bonus
    cover_bonus = 10 if len(cover_letter) > 100 else 0

    fit_score = int(min(keyword_score * 0.6 + length_score + cover_bonus, 100))

    strengths = []
    gaps = []
    suggestions = []

    if len(resume_text) > 500:
        strengths.append("Resume has sufficient detail and length")
    else:
        gaps.append("Resume is quite brief — consider adding more detail")

    if cover_letter:
        strengths.append("Cover letter provided")
    else:
        gaps.append("No cover letter submitted")
        suggestions.append("Include a cover letter tailored to this specific role")

    if keyword_score > 40:
        strengths.append("Good keyword alignment with the job description")
    else:
        gaps.append("Limited alignment between resume content and job requirements")
        suggestions.append(
            "Review the job description and ensure your resume addresses "
            "the key requirements and skills mentioned"
        )

    tailoring = (
        "This resume appears well-targeted to the role."
        if keyword_score > 50
        else "This resume appears generic and could benefit from tailoring "
        "to the specific requirements of this position."
    )

    return AssessmentResult(
        fit_score=fit_score,
        feedback=FeedbackDetail(
            strengths=strengths,
            gaps=gaps,
            suggestions=suggestions,
            tailoring=tailoring,
        ),
        proceed_to_interview=fit_score >= 50,
        message=(
            "Your application looks strong — proceed to the interview stage!"
            if fit_score >= 50
            else "Your application needs improvement. Review the feedback "
            "and consider revising your resume before reapplying."
        ),
    )


async def assess_with_llm(
    resume_text: str,
    cover_letter: str,
    job_title: str,
    job_description: str,
) -> AssessmentResult:
    """Assess a resume using a local LLM via Ollama API.

    Requires: pip install httpx
    Set OLLAMA_BASE_URL env var (default: http://localhost:11434)
    Set OLLAMA_MODEL env var (default: llama3.2)
    """
    import httpx

    base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    model = os.environ.get("OLLAMA_MODEL", "llama3.2")

    system_prompt = """You are an HR recruitment assistant assessing a resume
against a job posting. Provide a structured assessment in JSON format with
these exact fields:

{
  "fit_score": <0-100>,
  "strengths": ["..."],
  "gaps": ["..."],
  "suggestions": ["..."],
  "tailoring": "One sentence on how well the resume targets this specific role",
  "proceed_to_interview": true/false
}

Be realistic but encouraging. This is for an educational simulation where
students are learning to write resumes. Focus on actionable feedback."""

    user_prompt = f"""Assess this resume for the following position:

JOB TITLE: {job_title}

JOB DESCRIPTION:
{job_description[:3000]}

RESUME:
{resume_text[:3000]}

COVER LETTER:
{cover_letter[:1000] if cover_letter else "(none provided)"}

Return ONLY valid JSON matching the schema above."""

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            f"{base_url}/api/generate",
            json={
                "model": model,
                "prompt": user_prompt,
                "system": system_prompt,
                "stream": False,
                "format": "json",
            },
        )
        resp.raise_for_status()

    result_text = resp.json().get("response", "{}")
    data: dict[str, Any] = json.loads(result_text)

    return AssessmentResult(
        fit_score=int(data.get("fit_score", 50)),
        feedback=FeedbackDetail(
            strengths=data.get("strengths", []),
            gaps=data.get("gaps", []),
            suggestions=data.get("suggestions", []),
            tailoring=data.get("tailoring", ""),
        ),
        proceed_to_interview=data.get("proceed_to_interview", False),
        message=(
            "Your application looks strong — proceed to the interview stage!"
            if data.get("proceed_to_interview")
            else "Your application needs improvement. Review the feedback "
            "and consider revising your resume before reapplying."
        ),
    )

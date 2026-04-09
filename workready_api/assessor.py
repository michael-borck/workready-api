"""Resume assessment logic.

Supports multiple LLM backends:
- stub:       Rule-based (no LLM, for development)
- ollama:     Local or remote Ollama instance
- anthropic:  Anthropic API (Claude)
- openrouter: OpenRouter API (any model)

Set LLM_PROVIDER env var to choose. Default: stub.
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx

from workready_api.models import AssessmentResult, FeedbackDetail

SYSTEM_PROMPT = """You are an HR recruitment assistant assessing a resume
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


def _build_user_prompt(
    resume_text: str,
    cover_letter: str,
    job_title: str,
    job_description: str,
) -> str:
    return f"""Assess this resume for the following position:

JOB TITLE: {job_title}

JOB DESCRIPTION:
{job_description[:3000]}

RESUME:
{resume_text[:3000]}

COVER LETTER:
{cover_letter[:1000] if cover_letter else "(none provided)"}

Return ONLY valid JSON matching the schema above."""


def _parse_llm_response(text: str) -> AssessmentResult:
    """Parse LLM JSON response into an AssessmentResult."""
    # Strip markdown code fences if present
    cleaned = text.strip()
    if cleaned.startswith("```"):
        first_nl = cleaned.find("\n")
        if first_nl != -1:
            cleaned = cleaned[first_nl + 1:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()

    data: dict[str, Any] = json.loads(cleaned)

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


# --- Stub (no LLM) ---


def assess_stub(
    resume_text: str,
    cover_letter: str,
    job_title: str,
    job_description: str,
) -> AssessmentResult:
    """Rule-based stub assessment for development and testing."""
    resume_lower = resume_text.lower()
    job_lower = job_description.lower()

    job_keywords = {w for w in job_lower.split() if len(w) > 4}
    resume_words = set(resume_lower.split())
    overlap = job_keywords & resume_words
    keyword_score = min(len(overlap) / max(len(job_keywords), 1) * 100, 100)

    length_score = min(len(resume_text) / 20, 30)
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


# --- Ollama (local or remote) ---


async def assess_with_ollama(
    resume_text: str,
    cover_letter: str,
    job_title: str,
    job_description: str,
) -> AssessmentResult:
    """Assess via Ollama API. Supports bearer token for remote instances."""
    base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    model = os.environ.get("LLM_MODEL", "llama3.2")
    api_key = os.environ.get("OLLAMA_API_KEY", "")

    headers: dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    user_prompt = _build_user_prompt(resume_text, cover_letter, job_title, job_description)

    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            f"{base_url}/api/generate",
            json={
                "model": model,
                "prompt": user_prompt,
                "system": SYSTEM_PROMPT,
                "stream": False,
                "format": "json",
            },
            headers=headers,
        )
        resp.raise_for_status()

    return _parse_llm_response(resp.json().get("response", "{}"))


# --- Anthropic (Claude) ---


async def assess_with_anthropic(
    resume_text: str,
    cover_letter: str,
    job_title: str,
    job_description: str,
) -> AssessmentResult:
    """Assess via Anthropic API."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    model = os.environ.get("LLM_MODEL", "claude-sonnet-4-20250514")

    user_prompt = _build_user_prompt(resume_text, cover_letter, job_title, job_description)

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            "https://api.anthropic.com/v1/messages",
            json={
                "model": model,
                "max_tokens": 1024,
                "system": SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": user_prompt}],
            },
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
        )
        resp.raise_for_status()

    content = resp.json()["content"][0]["text"]
    return _parse_llm_response(content)


# --- OpenRouter ---


async def assess_with_openrouter(
    resume_text: str,
    cover_letter: str,
    job_title: str,
    job_description: str,
) -> AssessmentResult:
    """Assess via OpenRouter API (OpenAI-compatible)."""
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    model = os.environ.get("LLM_MODEL", "anthropic/claude-sonnet-4")

    user_prompt = _build_user_prompt(resume_text, cover_letter, job_title, job_description)

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                "response_format": {"type": "json_object"},
            },
            headers={
                "Authorization": f"Bearer {api_key}",
                "content-type": "application/json",
            },
        )
        resp.raise_for_status()

    content = resp.json()["choices"][0]["message"]["content"]
    return _parse_llm_response(content)


# --- Dispatcher ---

PROVIDERS = {
    "stub": None,
    "ollama": assess_with_ollama,
    "anthropic": assess_with_anthropic,
    "openrouter": assess_with_openrouter,
}


async def assess(
    resume_text: str,
    cover_letter: str,
    job_title: str,
    job_description: str,
) -> AssessmentResult:
    """Assess a resume using the configured LLM provider."""
    provider = os.environ.get("LLM_PROVIDER", "stub").lower()

    if provider == "stub" or provider not in PROVIDERS:
        return assess_stub(resume_text, cover_letter, job_title, job_description)

    handler = PROVIDERS[provider]
    return await handler(resume_text, cover_letter, job_title, job_description)  # type: ignore[misc]

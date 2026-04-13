"""Stage 4b — Mentor review of a task submission.

The mentor (the `reports_to` character from the job listing) reviews the
student's submission and produces structured feedback: score, outcome
(passed|failed|resubmit), strengths, improvements, summary.

Mirrors the pattern in interview.py: async chat_completion dispatch,
JSON-mode prompt, manual JSON parse with markdown-fence stripping, and
a deterministic stub for LLM_PROVIDER=stub development.
"""

from __future__ import annotations

import json
import os
from typing import Any

from workready_api.interview import chat_completion
from workready_api.models import TaskFeedback


REVIEW_SYSTEM_PROMPT_TEMPLATE = """{manager_persona}

═══════════════════════════════════════════════════════════
You are reviewing a work task submission from a student intern at {company_name}.
You are their mentor for this internship — the same character who
interviewed them. Stay in character throughout your review.

This is an educational simulation. Be realistic but encouraging. The
student is learning. Focus on actionable feedback they can apply to
the next task — they will be given the next brief shortly after
yours lands in their inbox.

Return your review as JSON with these exact fields:

{{
  "score": <0-100>,
  "outcome": "passed" | "resubmit" | "failed",
  "strengths": ["...", "..."],
  "improvements": ["...", "..."],
  "summary": "2-3 sentence message you'd write to the student, in your voice"
}}

Scoring guide:
  80-100 → passed (strong work, clear next steps are just refinements)
  55-79  → passed (solid enough; note what to strengthen)
  40-54  → resubmit (meaningful gaps; student should revise and resubmit)
  0-39   → failed (didn't meet the brief; we'll move on to the next task anyway)

Only use "failed" when the submission genuinely misses the brief. Use
"resubmit" when there's a meaningful gap worth closing. Use "passed"
when the work is good enough to move forward, even if imperfect.
"""


def build_review_system_prompt(manager_persona: str, company_name: str) -> str:
    return REVIEW_SYSTEM_PROMPT_TEMPLATE.format(
        manager_persona=manager_persona or "You are an experienced mentor.",
        company_name=company_name or "the company",
    )


def build_review_user_prompt(
    task_title: str,
    task_brief: str,
    task_description: str,
    submission_body: str,
    attachment_text: str | None,
    difficulty: str,
    prior_history: list[dict[str, Any]],
    late_by_days: int = 0,
) -> str:
    """Assemble the user-turn prompt for the reviewer LLM call."""
    history_block = ""
    if prior_history:
        lines = ["PRIOR TASKS THIS STUDENT HAS COMPLETED IN THIS INTERNSHIP:"]
        for t in prior_history:
            sub = t.get("submission") or {}
            score = sub.get("score")
            outcome = sub.get("review_status") or t.get("status") or "n/a"
            lines.append(
                f"  {t['sequence']}. {t['title']} ({t['difficulty']}) — "
                f"outcome: {outcome}"
                + (f", score: {score}" if score is not None else "")
            )
        history_block = "\n" + "\n".join(lines) + "\n"

    attachment_block = ""
    if attachment_text and attachment_text.strip():
        attachment_block = (
            f"\n\nATTACHED DOCUMENT (extracted text):\n"
            f'"""\n{attachment_text[:4000]}\n"""'
        )

    late_block = ""
    if late_by_days > 0:
        late_block = (
            f"\n\nNOTE: This submission is {late_by_days} day(s) past the "
            f"deadline. You may comment on that naturally as part of your feedback."
        )

    return f"""Review this work task submission.

TASK: {task_title} ({difficulty})
BRIEF: {task_brief}

FULL DESCRIPTION:
\"\"\"
{task_description[:2000]}
\"\"\"
{history_block}
STUDENT SUBMISSION (body):
\"\"\"
{submission_body[:4000]}
\"\"\"{attachment_block}{late_block}

Return ONLY valid JSON matching the schema above."""


async def review_task_submission(
    *,
    manager_persona: str,
    company_name: str,
    task_title: str,
    task_brief: str,
    task_description: str,
    difficulty: str,
    submission_body: str,
    attachment_text: str | None,
    prior_history: list[dict[str, Any]],
    late_by_days: int = 0,
) -> tuple[int, str, TaskFeedback]:
    """Run the mentor review LLM call.

    Returns (score, outcome, feedback). outcome is one of
    "passed" | "resubmit" | "failed".
    """
    provider = os.environ.get("LLM_PROVIDER", "stub").lower()

    if provider == "stub":
        return _stub_review(submission_body, difficulty)

    system_prompt = build_review_system_prompt(manager_persona, company_name)
    user_prompt = build_review_user_prompt(
        task_title=task_title,
        task_brief=task_brief,
        task_description=task_description,
        submission_body=submission_body,
        attachment_text=attachment_text,
        difficulty=difficulty,
        prior_history=prior_history,
        late_by_days=late_by_days,
    )
    raw = await chat_completion(
        system_prompt,
        [{"role": "user", "content": user_prompt}],
    )
    return _parse_review(raw)


def _stub_review(body: str, difficulty: str) -> tuple[int, str, TaskFeedback]:
    """Deterministic review for LLM_PROVIDER=stub."""
    word_count = len(body.split())
    base = {"easy": 60, "medium": 55, "hard": 50}.get(difficulty, 55)
    score = min(100, base + min(word_count // 10, 35))

    if score >= 55:
        outcome = "passed"
        summary = (
            "Solid first pass. You've covered the main points and the tone "
            "is about right. Keep this quality up and push a little further "
            "on the next one."
        )
    elif score >= 40:
        outcome = "resubmit"
        summary = (
            "There's a good foundation here but it needs more development "
            "before I'd send it on. Have another go and resubmit."
        )
    else:
        outcome = "failed"
        summary = (
            "This one didn't land. Don't worry — I'll set you up with the "
            "next brief and we'll regroup."
        )

    return (
        score,
        outcome,
        TaskFeedback(
            strengths=(
                ["Engaged with the brief", "Reasonable structure"]
                if score >= 55 else ["Made an attempt"]
            ),
            improvements=(
                ["Add specific examples or data where you can",
                 "Tighten your opening paragraph"]
                if score < 80 else ["Minor polish only"]
            ),
            summary=summary,
        ),
    )


def _parse_review(raw: str) -> tuple[int, str, TaskFeedback]:
    """Parse the reviewer LLM's JSON response."""
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        first_nl = cleaned.find("\n")
        if first_nl != -1:
            cleaned = cleaned[first_nl + 1:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()

    data: dict[str, Any] = json.loads(cleaned)

    score = int(data.get("score", 50))
    outcome_raw = str(data.get("outcome", "passed")).lower().strip()
    if outcome_raw not in ("passed", "resubmit", "failed"):
        outcome_raw = "passed" if score >= 55 else "resubmit" if score >= 40 else "failed"

    feedback = TaskFeedback(
        strengths=list(data.get("strengths", []) or []),
        improvements=list(data.get("improvements", []) or []),
        summary=str(data.get("summary", "")),
    )
    return score, outcome_raw, feedback

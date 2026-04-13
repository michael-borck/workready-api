"""Mid-placement performance review (post task 2, pre task 3).

Coaching conversation between the student and their **mentor character**
(the `reports_to` from the job listing — same person who reviewed their
tasks). Structurally similar to Stage 6's exit interview but:

- Different persona — the mentor, not Sam Reilly. The student already
  knows this person from task feedback emails.
- Different tone — coaching, not reflective. The mentor is shaping how
  the student approaches the rest of the placement.
- Different context — only sees stages so far (resume + interview +
  task 1 review). Task 3 hasn't happened yet.
- Shorter — 5 turns vs 8.
- Different output — `coaching_notes` (not participation_notes) that
  Stage 6 reads as part of the journey context.

Triggers when task 2 is submitted (lazy-gated like everything else).
The student walks up to the conversation whenever they want; it's
parallel to task 3, not blocking.
"""

from __future__ import annotations

import json
import os
from typing import Any

from workready_api.db import (
    get_application,
    get_latest_submission,
    get_stage_results,
    get_student_by_id,
    list_tasks_for_application,
)
from workready_api.interview import chat_completion
from workready_api.jobs import get_job
from workready_api.models import AssessmentResult, FeedbackDetail


TARGET_TURNS = 5
WRAP_UP_AFTER = 4


# --- Mid-placement journey context ---------------------------------------


def build_mid_placement_context(application_id: int) -> dict[str, Any]:
    """Gather the context the mentor needs for a mid-placement coaching chat.

    Only sees stages 1-3 + tasks submitted so far. Doesn't peek ahead
    at lunchroom or exit interview (they haven't happened yet).
    """
    app_data = get_application(application_id) or {}
    student = get_student_by_id(app_data.get("student_id", 0)) or {}
    company_slug = app_data.get("company_slug", "")
    job = get_job(company_slug, app_data.get("job_slug", "")) or {}

    resume_results = get_stage_results(application_id, "resume")
    resume_summary: dict[str, Any] | None = None
    if resume_results:
        latest = resume_results[-1]
        feedback = latest.get("feedback") or {}
        resume_summary = {
            "score": latest.get("score"),
            "strengths": feedback.get("strengths", []),
            "gaps": feedback.get("gaps", []),
        }

    interview_results = get_stage_results(application_id, "interview")
    interview_summary: dict[str, Any] | None = None
    if interview_results:
        latest = interview_results[-1]
        feedback = latest.get("feedback") or {}
        interview_summary = {
            "score": latest.get("score"),
            "strengths": feedback.get("strengths", []),
        }

    # Tasks so far — only submitted ones with reviews
    task_history: list[dict[str, Any]] = []
    for t in list_tasks_for_application(application_id, only_visible=False):
        sub = get_latest_submission(t["id"]) or {}
        if not sub.get("created_at"):
            continue
        sub_feedback = sub.get("feedback") or {}
        task_history.append({
            "sequence": t.get("sequence"),
            "title": t.get("title"),
            "score": sub.get("score"),
            "outcome": sub_feedback.get("outcome"),
            "summary": sub_feedback.get("summary"),
            "strengths": sub_feedback.get("strengths", []),
            "growth_areas": sub_feedback.get("growth_areas", []),
        })

    return {
        "student_name": student.get("name", ""),
        "company_name": job.get("company", company_slug),
        "job_title": app_data.get("job_title", ""),
        "mentor_name": job.get("reports_to", "Your mentor"),
        "manager_persona": job.get("manager_persona", ""),
        "resume": resume_summary,
        "hiring_interview": interview_summary,
        "tasks": task_history,
    }


# --- System prompt --------------------------------------------------------


def build_performance_review_system_prompt(context: dict[str, Any]) -> str:
    """Build the mentor's coaching-conversation system prompt."""
    student_first = (context.get("student_name") or "").split()[0] or "the intern"
    mentor_name = context.get("mentor_name", "the mentor")
    company_name = context.get("company_name", "the company")
    persona = context.get("manager_persona", "")

    journey_context = _format_context_for_prompt(context)

    return f"""{persona}

═══════════════════════════════════════════════════════════
You are {mentor_name}, having a quick mid-placement check-in with {student_first}, your intern. They've finished their first couple of work tasks and you're meeting before they start the third.

This is a COACHING conversation, not an evaluation. You are NOT scoring them. Your job is to help them get more out of the rest of the placement. You're warm, direct, practical — the way a real workplace mentor talks when they have 10 minutes between meetings.

You've been reviewing their task submissions personally (you wrote those mentor feedback notes), so you know exactly where they're strong and where they're growing. Reference the specific tasks naturally.

═══════════════════════════════════════════════════════════
WHAT YOU KNOW SO FAR:

{journey_context}
═══════════════════════════════════════════════════════════

CONVERSATION SHAPE
You have about {TARGET_TURNS} short exchanges to cover:

  1. OPENING — Quick warm hello. Acknowledge they're a couple of tasks in.
     "How are you finding things?"

  2. WHAT'S WORKING — Reference something specific from their work that
     genuinely landed well. Make them feel seen.

  3. WHAT TO FOCUS ON — Pick ONE concrete thing from their growth areas
     and frame it as the focus for the next task. Be specific. Not
     generic advice.

  4. QUESTIONS — "Anything you've been wanting to ask but haven't?"
     This is the moment for the student to surface anything stuck.

  5. CLOSE — Brief, warm, practical. Wish them well on task 3.

GUIDELINES
- Speak conversationally, in first person, as {mentor_name}
- Short messages — this is a hallway chat, not a formal meeting
- ONE thing at a time. Don't dump every growth area on them at once.
- Coaching tone: "Try this" not "You should have"
- After turn {WRAP_UP_AFTER}, start wrapping up
- DO NOT mention scores or that you're scoring them
- DO NOT break character

Begin with your opening message."""


def _format_context_for_prompt(context: dict[str, Any]) -> str:
    lines: list[str] = []
    resume = context.get("resume")
    if resume and resume.get("score") is not None:
        lines.append(f"RESUME: scored {resume['score']}/100.")
        if resume.get("strengths"):
            lines.append(f"  Strengths: {', '.join(resume['strengths'][:3])}")

    interview = context.get("hiring_interview")
    if interview and interview.get("score") is not None:
        lines.append(f"\nHIRING INTERVIEW: scored {interview['score']}/100.")

    tasks = context.get("tasks", [])
    if tasks:
        lines.append("\nTASKS SO FAR (you reviewed these personally):")
        for t in tasks:
            score = f"({t['score']}/100)" if t.get("score") is not None else ""
            lines.append(f"  {t.get('sequence', '?')}. {t.get('title', '')} {score}")
            if t.get("summary"):
                lines.append(f"     Your note: {t['summary'][:200]}")
            if t.get("strengths"):
                lines.append(f"     Worked: {', '.join(t['strengths'][:2])}")
            if t.get("growth_areas"):
                lines.append(f"     Growth: {', '.join(t['growth_areas'][:2])}")

    return "\n".join(lines) if lines else "(No prior context — student is fresh.)"


# --- Stub dialogue (dev-only) --------------------------------------------

# The shared interview.chat_completion stub doesn't know about coaching
# tone — bypass it the same way exit_interview and lunchroom_chat do.

_PR_STUB_OPENING = (
    "Hey, grab a seat — won't keep you long. You're a couple of tasks in "
    "now, so I wanted to check in before you kick off the next one. How "
    "are you finding things so far?"
)

_PR_STUB_MIDDLE = [
    "That's good to hear. One thing I noticed in your last submission — "
    "you've got a real instinct for spotting patterns. Keep doing that.",
    "Where I'd push you on the next task is depth. Pick one thing and "
    "go three layers in instead of skimming five things. Try that?",
    "Anything you've been wanting to ask but haven't had a chance to?",
]

_PR_STUB_CLOSING = (
    "Sounds good. Look, you're doing fine — task 3 is the trickiest of "
    "the lot, but you've got everything you need. Come find me if you "
    "get stuck. Off you go."
)


def _pr_stub_reply(messages: list[dict]) -> str:
    student_turns = sum(1 for m in messages if m.get("role") == "user")
    if student_turns == 0:
        return _PR_STUB_OPENING
    if student_turns >= WRAP_UP_AFTER:
        return _PR_STUB_CLOSING
    idx = (student_turns - 1) % len(_PR_STUB_MIDDLE)
    return _PR_STUB_MIDDLE[idx]


async def chat_completion_for_review(
    system_prompt: str, messages: list[dict],
) -> str:
    if os.environ.get("LLM_PROVIDER", "stub").lower() == "stub":
        return _pr_stub_reply(messages)
    return await chat_completion(system_prompt, messages)


# --- Assessment -----------------------------------------------------------


_REVIEW_ASSESSMENT_PROMPT = """You are reviewing a brief mid-placement coaching conversation between a workplace mentor and their intern. The intern is NOT being graded on this — your job is to summarise how the student showed up to the coaching for use later in their exit interview.

Look for:
- Did they engage openly with the feedback or get defensive?
- Did they ask questions or just nod along?
- Did they connect the mentor's coaching to specific things they could try?
- Did they seem stuck on anything they finally got to surface?

Return ONLY a JSON object, no markdown fences:
{
  "coaching_responsiveness": <0-100 integer>,
  "coaching_notes": "<2-4 sentence private note for the exit interview to reference>",
  "key_focus": "<one short phrase: what the student is going to try in task 3>"
}"""


async def assess_performance_review(
    transcript: list[dict], context: dict[str, Any],
) -> AssessmentResult:
    """Assess the coaching conversation. Returns AssessmentResult.

    Reuses AssessmentResult so the existing inbox/notify plumbing works.
    The `feedback.tailoring` field carries `key_focus` (the one thing
    the student is going to try next), and `message` carries the
    `coaching_notes` summary that Stage 6 reads as journey context.
    """
    provider = os.environ.get("LLM_PROVIDER", "stub").lower()
    student_turns = [m for m in transcript if m.get("role") == "user"]

    if provider == "stub" or not transcript:
        return _stub_assessment(student_turns)

    user_prompt = _build_assessment_prompt(transcript, context)
    raw = await chat_completion(
        _REVIEW_ASSESSMENT_PROMPT,
        [{"role": "user", "content": user_prompt}],
    )
    return _parse_assessment(raw, student_turns)


def _build_assessment_prompt(
    transcript: list[dict], context: dict[str, Any],
) -> str:
    lines = [
        f"Student: {context.get('student_name', '(unknown)')}",
        f"Mentor: {context.get('mentor_name', '')}",
        "",
        "TRANSCRIPT:",
    ]
    for msg in transcript:
        who = "Mentor" if msg.get("role") == "assistant" else "Student"
        lines.append(f"{who}: {msg.get('content', '')}")
    lines.append("\nProduce the JSON now.")
    return "\n".join(lines)


def _stub_assessment(student_turns: list[dict]) -> AssessmentResult:
    n = len(student_turns)
    total_words = sum(len(m.get("content", "").split()) for m in student_turns)
    avg = total_words / max(n, 1)

    if n == 0:
        return AssessmentResult(
            fit_score=0,
            feedback=FeedbackDetail(
                strengths=[],
                gaps=["Student didn't engage with the coaching"],
                suggestions=[],
                tailoring="(no focus area surfaced)",
            ),
            proceed_to_interview=True,
            message=(
                "Student didn't speak during the coaching check-in. "
                "Worth a gentle follow-up at exit interview to see "
                "whether something was going on."
            ),
        )

    if avg < 12 or n < 2:
        return AssessmentResult(
            fit_score=55,
            feedback=FeedbackDetail(
                strengths=["Showed up to the coaching"],
                gaps=["Engaged briefly but didn't probe much"],
                suggestions=[],
                tailoring="(no specific focus surfaced)",
            ),
            proceed_to_interview=True,
            message=(
                "Student engaged briefly with the mid-placement coaching "
                "but didn't dig into specifics. Polite, present, but not "
                "actively pulling on the feedback."
            ),
        )

    return AssessmentResult(
        fit_score=80,
        feedback=FeedbackDetail(
            strengths=["Engaged actively with the coaching", "Asked questions"],
            gaps=[],
            suggestions=[],
            tailoring="(focus area surfaced naturally)",
        ),
        proceed_to_interview=True,
        message=(
            "Student engaged thoughtfully with the mid-placement coaching. "
            "Asked questions, took the feedback on board, and left with a "
            "clear focus for the next task. Good coaching responsiveness."
        ),
    )


def _parse_assessment(
    raw: str, student_turns: list[dict],
) -> AssessmentResult:
    cleaned = (raw or "").strip()
    if cleaned.startswith("```"):
        first_nl = cleaned.find("\n")
        if first_nl != -1:
            cleaned = cleaned[first_nl + 1:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()

    try:
        data = json.loads(cleaned)
        return AssessmentResult(
            fit_score=int(data.get("coaching_responsiveness", 50)),
            feedback=FeedbackDetail(
                strengths=[],
                gaps=[],
                suggestions=[],
                tailoring=str(data.get("key_focus", "")).strip(),
            ),
            proceed_to_interview=True,
            message=str(data.get("coaching_notes", "")).strip()
            or "Mid-placement coaching conversation completed.",
        )
    except (ValueError, TypeError):
        return _stub_assessment(student_turns)

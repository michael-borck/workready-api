"""Stage 6 — Exit interview.

Reflective interview at the end of the internship. Conducted by a
different character than the hiring manager — typically HR or a senior
leader — and the conversation is *reflective*, not evaluative. The
focus is on what the student learned, what they'd do differently, how
they found the team, and what they'd give as feedback. The assessment
scores **self-awareness** rather than performance.

The interviewer has access to the full student journey:
- Resume score and feedback
- Hiring interview score and feedback
- All task submissions, scores, and mentor feedback
- Lunchroom participation notes (private observations from Stage 5c)

This context lets the interviewer reference specific moments naturally
("I saw your task on the supplier risk matrix scored well — what did
you learn working on that?") rather than asking generic questions.

Mirrors `interview.py` shape: build_*_system_prompt, chat_completion
reuse, assess_* with a stub fallback, _parse_* with JSON-fence stripping.
"""

from __future__ import annotations

import json
import os
from typing import Any

from workready_api.db import (
    get_active_performance_review,
    get_application,
    get_latest_submission,
    get_stage_results,
    get_student_by_id,
    list_lunchroom_sessions_for_application,
    list_tasks_for_application,
)
from workready_api.interview import chat_completion
from workready_api.jobs import get_job
from workready_api.models import AssessmentResult, FeedbackDetail


TARGET_TURNS = 8
WRAP_UP_AFTER = 6


# --- Stub dialogue (dev-only) --------------------------------------------

# The shared interview.chat_completion stub is tuned for hiring interviews
# ("tell me about your strengths") which is wrong for the reflective exit
# format. Bypass it in stub mode with a hand-written reflective arc.

_EXIT_STUB_OPENING = (
    "Hi, thanks for taking the time to sit down with me. I'm Sam from "
    "People & Culture — I wasn't part of your day-to-day, so this is just "
    "a chance for you to reflect, not a test. To start with, how did you "
    "find your placement overall? What stood out?"
)

_EXIT_STUB_MIDDLE = [
    "That's good to hear. Can you tell me about a moment from the "
    "placement that really stuck with you?",
    "Looking back, is there anything you'd approach differently if you "
    "could do it again?",
    "How did you find getting to know the team? Were there moments where "
    "it clicked?",
    "What would you say you learned about yourself through this experience?",
    "Is there anything you wish we'd done differently as a team to "
    "support you better?",
]

_EXIT_STUB_CLOSING = (
    "Thanks for being so honest with me. That's everything I wanted to "
    "ask — take care of yourself, and the very best of luck with what "
    "comes next."
)


def _exit_stub_reply(messages: list[dict]) -> str:
    """Deterministic reflective stub for dev mode."""
    student_turns = sum(1 for m in messages if m.get("role") == "user")
    if student_turns == 0:
        return _EXIT_STUB_OPENING
    if student_turns >= WRAP_UP_AFTER:
        return _EXIT_STUB_CLOSING
    idx = (student_turns - 1) % len(_EXIT_STUB_MIDDLE)
    return _EXIT_STUB_MIDDLE[idx]


async def chat_completion_for_exit(
    system_prompt: str, messages: list[dict],
) -> str:
    """Wrapper around the shared chat_completion that injects an
    exit-interview-flavoured stub when LLM_PROVIDER=stub. In production
    (any non-stub provider) the system prompt is sent as-is and the
    LLM speaks as Sam Reilly per the prompt instructions.
    """
    if os.environ.get("LLM_PROVIDER", "stub").lower() == "stub":
        return _exit_stub_reply(messages)
    return await chat_completion(system_prompt, messages)


# --- Journey context gathering --------------------------------------------


def build_journey_context(application_id: int) -> dict[str, Any]:
    """Gather everything the exit interviewer needs to know about the student.

    Returns a dict with structured data — the system prompt builder
    flattens it into prose. Returning a dict (not a string) keeps this
    testable and lets the assessor reuse the same data later.
    """
    app_data = get_application(application_id) or {}
    student = get_student_by_id(app_data.get("student_id", 0)) or {}
    company_slug = app_data.get("company_slug", "")
    job_slug = app_data.get("job_slug", "")
    job = get_job(company_slug, job_slug) or {}

    # Resume stage
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

    # Hiring interview stage
    interview_results = get_stage_results(application_id, "interview")
    interview_summary: dict[str, Any] | None = None
    if interview_results:
        latest = interview_results[-1]
        feedback = latest.get("feedback") or {}
        interview_summary = {
            "score": latest.get("score"),
            "strengths": feedback.get("strengths", []),
            "gaps": feedback.get("gaps", []),
        }

    # Tasks (Stage 4)
    task_history: list[dict[str, Any]] = []
    for t in list_tasks_for_application(application_id, only_visible=False):
        sub = get_latest_submission(t["id"]) or {}
        sub_feedback = sub.get("feedback") or {}
        task_history.append({
            "sequence": t.get("sequence"),
            "title": t.get("title"),
            "status": t.get("status"),
            "score": sub.get("score"),
            "outcome": sub_feedback.get("outcome"),
            "summary": sub_feedback.get("summary"),
            "strengths": sub_feedback.get("strengths", []),
            "growth_areas": sub_feedback.get("growth_areas", []),
        })

    # Lunchroom (Stage 5)
    lunchroom_summary: list[dict[str, Any]] = []
    for sess in list_lunchroom_sessions_for_application(application_id):
        lunchroom_summary.append({
            "occasion": sess.get("occasion"),
            "status": sess.get("status"),
            "participation_notes": sess.get("participation_notes"),
        })

    # Mid-placement performance review (between task 2 and task 3) —
    # Sam Reilly references this naturally if it happened.
    perf_review_summary: dict[str, Any] | None = None
    perf_session = get_active_performance_review(application_id)
    if perf_session and perf_session.get("status") == "completed":
        feedback = perf_session.get("feedback") or {}
        perf_review_summary = {
            "coaching_notes": feedback.get("summary", ""),
            "key_focus": feedback.get("key_focus", ""),
            "responsiveness_score": feedback.get("fit_score"),
        }

    return {
        "student_name": student.get("name", ""),
        "company_name": job.get("company", company_slug),
        "job_title": app_data.get("job_title", job_slug),
        "manager_name": job.get("reports_to", ""),
        "resume": resume_summary,
        "hiring_interview": interview_summary,
        "tasks": task_history,
        "lunchroom": lunchroom_summary,
        "performance_review": perf_review_summary,
    }


# --- System prompt --------------------------------------------------------


def build_exit_interview_system_prompt(journey: dict[str, Any]) -> str:
    """Build the reflective HR/senior-leader system prompt.

    Crucially, this is NOT the hiring manager. The exit interview is
    conducted by someone the student hasn't necessarily worked closely
    with — gives the student room to speak honestly without performing
    for the same person who reviewed their tasks.
    """
    student_first = (journey.get("student_name") or "").split()[0] or "the intern"
    company_name = journey.get("company_name", "the company")
    job_title = journey.get("job_title", "the role")

    journey_context = _format_journey_for_prompt(journey)

    return f"""You are Sam Reilly, Head of People at {company_name}. You're conducting an end-of-placement exit interview with {student_first}, who has just finished their internship as a {job_title}.

This is a REFLECTIVE conversation, not an evaluation. Your goal is to help {student_first} think back on their experience and articulate what they learned. Be warm, curious, and a little informal — you want them to feel safe being honest. You are not their mentor and you weren't grading their tasks, so the student should feel free to say things they wouldn't say to {journey.get('manager_name') or 'their direct mentor'}.

You have read their full file and you know what happened during the placement. Reference specific moments naturally — don't ask generic questions when you can ask about something real that happened. But don't read the file at them.

═══════════════════════════════════════════════════════════
WHAT YOU KNOW ABOUT {student_first.upper()}'S PLACEMENT:

{journey_context}
═══════════════════════════════════════════════════════════

CONVERSATION SHAPE
You have about {TARGET_TURNS} exchanges to cover these areas naturally:

  1. OPENING — Warm welcome. Thank them for their time. Note that
     this isn't a test, it's a chance to reflect.

  2. THE EXPERIENCE — "How did you find the placement overall? What
     stood out?" Listen carefully. Probe gently on anything interesting.

  3. SPECIFIC LEARNING — Reference one of their actual tasks or
     moments. "I saw you worked on X — what did you learn from that?"
     Be specific. The student should feel seen.

  4. WHAT YOU'D DO DIFFERENTLY — "Looking back, is there anything you
     would have approached differently?" This is the self-awareness
     question. How they answer matters more than what they answer.

  5. THE TEAM — "How did you find getting to know the team?" Reference
     the lunchroom moments naturally if relevant — but supportively,
     never as a "gotcha" if they didn't engage much.

  6. FEEDBACK FOR US — "Is there anything we could have done better
     to support you?" Encourage honesty.

  7. CLOSE — Thank them warmly. Wish them well.

GUIDELINES
- Speak conversationally, in first person, as Sam Reilly
- Ask ONE question at a time
- Adapt to what they say — follow up on real things
- Be warm but don't fawn. Be honest but never harsh.
- This is reflection, not assessment — the student is not being graded
- After turn {WRAP_UP_AFTER}, start steering toward the close
- DO NOT mention scores or grades or the simulation
- DO NOT break character

Begin with your opening message."""


def _format_journey_for_prompt(journey: dict[str, Any]) -> str:
    """Flatten the journey dict into prose the LLM can reference."""
    lines: list[str] = []

    resume = journey.get("resume")
    if resume and resume.get("score") is not None:
        lines.append(f"RESUME: scored {resume['score']}/100 at application time.")
        if resume.get("strengths"):
            lines.append(f"  Strengths flagged: {', '.join(resume['strengths'][:3])}")
        if resume.get("gaps"):
            lines.append(f"  Gaps flagged: {', '.join(resume['gaps'][:3])}")

    interview = journey.get("hiring_interview")
    if interview and interview.get("score") is not None:
        lines.append(f"\nHIRING INTERVIEW: scored {interview['score']}/100.")
        if interview.get("strengths"):
            lines.append(
                f"  Came across well on: {', '.join(interview['strengths'][:3])}"
            )

    tasks = journey.get("tasks", [])
    if tasks:
        lines.append("\nWORK TASKS:")
        for t in tasks:
            score_part = f"({t['score']}/100)" if t.get("score") is not None else "(not scored)"
            lines.append(
                f"  {t.get('sequence', '?')}. {t.get('title', 'Task')} {score_part}"
            )
            if t.get("summary"):
                lines.append(f"     Mentor's note: {t['summary'][:200]}")
            if t.get("strengths"):
                lines.append(f"     What worked: {', '.join(t['strengths'][:2])}")
            if t.get("growth_areas"):
                lines.append(f"     Growth areas: {', '.join(t['growth_areas'][:2])}")

    lunchroom = journey.get("lunchroom", [])
    if lunchroom:
        lines.append("\nLUNCHROOM MOMENTS:")
        for sess in lunchroom:
            status = sess.get("status", "?")
            occasion = sess.get("occasion", "lunch")
            lines.append(f"  - {occasion} ({status})")
            if sess.get("participation_notes"):
                lines.append(f"    Notes: {sess['participation_notes'][:200]}")

    perf = journey.get("performance_review")
    if perf:
        lines.append("\nMID-PLACEMENT COACHING (with their mentor):")
        if perf.get("coaching_notes"):
            lines.append(f"  {perf['coaching_notes'][:300]}")
        if perf.get("key_focus"):
            lines.append(f"  Focus they took into task 3: {perf['key_focus']}")

    return "\n".join(lines) if lines else "(No journey data on file.)"


# --- Assessment -----------------------------------------------------------


_ASSESSMENT_SYSTEM_PROMPT = """You are reviewing an end-of-placement exit interview. The student is NOT being graded on their internship performance — that already happened through their tasks. Your job is to score their self-awareness and reflective ability based on this conversation.

Look for:
- Did they think before answering, or give surface-level responses?
- Did they own their growth areas, or deflect blame?
- Did they connect specific experiences to lessons learned?
- Did they show curiosity about feedback, or get defensive?
- Did they speak honestly about both highs and lows?

Return ONLY a JSON object with these exact keys, no markdown fences:
{
  "reflection_score": <0-100 integer>,
  "strengths": [<2-4 short phrases>],
  "growth_areas": [<2-4 short phrases>],
  "summary": "<1-2 sentence supportive close that the student will read>"
}

The summary is read by the student. Make it warm, specific, and forward-looking. Never shame. Never grade their internship performance — only their reflection in this conversation."""


async def assess_exit_interview(
    transcript: list[dict],
    journey: dict[str, Any],
) -> AssessmentResult:
    """Score the exit interview. Returns AssessmentResult.

    The result.message field is the warm summary shown to the student.
    Reuses AssessmentResult so existing inbox/notification plumbing
    works without a new model.
    """
    provider = os.environ.get("LLM_PROVIDER", "stub").lower()
    student_turns = [m for m in transcript if m.get("role") == "user"]

    if provider == "stub" or not transcript:
        return _stub_assessment(student_turns)

    user_prompt = _build_assessment_user_prompt(transcript, journey)
    raw = await chat_completion(
        _ASSESSMENT_SYSTEM_PROMPT,
        [{"role": "user", "content": user_prompt}],
    )
    return _parse_assessment(raw, student_turns)


def _build_assessment_user_prompt(
    transcript: list[dict], journey: dict[str, Any],
) -> str:
    """Format the transcript + journey context for the assessor LLM."""
    lines = [
        f"Student: {journey.get('student_name', '(unknown)')}",
        f"Role: {journey.get('job_title', '')} at {journey.get('company_name', '')}",
        "",
        "EXIT INTERVIEW TRANSCRIPT:",
    ]
    for msg in transcript:
        who = "Sam (HR)" if msg.get("role") == "assistant" else "Student"
        lines.append(f"{who}: {msg.get('content', '')}")
    lines.append("\nProduce the JSON now.")
    return "\n".join(lines)


def _stub_assessment(student_turns: list[dict]) -> AssessmentResult:
    """Deterministic fallback — three tiers based on engagement depth."""
    n = len(student_turns)
    total_words = sum(len(m.get("content", "").split()) for m in student_turns)
    avg = total_words / max(n, 1)

    if n == 0:
        return AssessmentResult(
            fit_score=0,
            feedback=FeedbackDetail(
                strengths=[],
                gaps=["No reflection was offered in the conversation"],
                suggestions=["Take more time to think through what you learned"],
                tailoring="",
            ),
            proceed_to_interview=True,
            message=(
                "We didn't get to hear much from you in this conversation, "
                "but that's okay — reflection takes practice. Take some "
                "time to think about your placement and what you'd carry "
                "forward. We're glad you were here."
            ),
        )

    if avg < 15 or n < 3:
        return AssessmentResult(
            fit_score=55,
            feedback=FeedbackDetail(
                strengths=["Engaged with the conversation"],
                gaps=[
                    "Answers were quite brief",
                    "Could connect specific moments to learning more directly",
                ],
                suggestions=[
                    "Use the STAR method when reflecting on experiences",
                ],
                tailoring="",
            ),
            proceed_to_interview=True,
            message=(
                "Thanks for taking the time to reflect with us. You showed "
                "up and engaged honestly — that's the hardest part. Next "
                "time you do something like this, try giving yourself "
                "permission to take a few seconds before each answer. The "
                "best reflection comes from thinking, not from speaking "
                "fast. Best of luck out there."
            ),
        )

    return AssessmentResult(
        fit_score=85,
        feedback=FeedbackDetail(
            strengths=[
                "Reflected thoughtfully on specific experiences",
                "Owned growth areas without deflecting",
                "Showed self-awareness about how the placement landed",
            ],
            gaps=[],
            suggestions=[
                "Carry this same reflective habit into your next role",
            ],
            tailoring="",
        ),
        proceed_to_interview=True,
        message=(
            "That was a genuinely thoughtful conversation — the kind of "
            "reflection that turns a placement into actual learning. You "
            "spoke honestly about both the wins and the harder bits, and "
            "you connected specific moments to specific lessons. Take "
            "that habit with you. We've enjoyed having you here."
        ),
    )


def _parse_assessment(
    raw: str, student_turns: list[dict],
) -> AssessmentResult:
    """Parse the LLM's JSON response, falling back to stub on error."""
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
            fit_score=int(data.get("reflection_score", 50)),
            feedback=FeedbackDetail(
                strengths=data.get("strengths", []),
                gaps=data.get("growth_areas", []),
                suggestions=[],
                tailoring="",
            ),
            proceed_to_interview=True,
            message=str(data.get("summary", "")).strip()
            or "Thanks for taking the time to reflect with us.",
        )
    except (ValueError, TypeError):
        return _stub_assessment(student_turns)

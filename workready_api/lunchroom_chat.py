"""Stage 5b — Lunchroom chat scheduler.

Twitch-style async group chat: 2-3 AI colleagues trickle messages into a
shared room, the student types whenever they like, and the whole thing
winds down after ~20 beats. No background worker — every post is a row
with a `deliver_at` timestamp and reads filter `WHERE deliver_at <= now()`.

Flow:

1. `activate(session_id)` — called when the student enters the room
   (from a poll hit on an accepted session whose scheduled_at window
   has opened). Plans a conversation arc: for each participant, 3-5
   beats, each a (character, intention) pair with a relative deliver_at.
   Persists beats as `lunchroom_posts` rows with status='pending' and
   content=NULL. Transitions session to 'active'.

2. `poll(session_id)` — called repeatedly by the portal. For each
   due pending post, renders the content via chat_completion() using
   the character's persona + current transcript as context, flips the
   row to 'delivered', returns all delivered rows. If the hard cap or
   the last beat has been delivered, transitions to 'completed'.

3. `post_student_message(session_id, text)` — inserts a delivered
   student row. If the text @mentions a character, pulls that
   character's next pending beat forward.
"""

from __future__ import annotations

import json
import os
import random
import re
from datetime import timedelta
from pathlib import Path
from typing import Any

from workready_api import scheduling
from workready_api.db import (
    count_delivered_posts,
    create_lunchroom_post,
    create_message,
    get_application,
    get_lunchroom_session,
    get_student_by_id,
    list_due_pending_posts,
    list_lunchroom_posts,
    mark_lunchroom_active,
    mark_lunchroom_completed,
    mark_post_delivered,
    next_pending_post_for_character,
    next_post_sequence,
    update_post_deliver_at,
)
from workready_api.interview import chat_completion
from workready_api.jobs import get_company, get_job


# --- Intention pools (fallback when LLM is stub) --------------------------

# Each intention is a short directive the LLM later renders into a line
# of natural chat. The stub picks these directly.
_GENERIC_INTENTIONS = [
    "open with a warm greeting to the group",
    "ask the intern how their week has been",
    "share a small observation about work today",
    "mention something about the occasion or context",
    "ask the intern a light getting-to-know-you question",
    "joke about a harmless workplace thing (coffee, traffic, weather)",
    "bring up something the team is working on at a high level",
    "react to what someone else said",
    "share a short opinion about a team dynamic",
    "ask the intern what they're finding most interesting so far",
    "wrap up warmly with a 'glad you joined us' note",
]

_OPENING_INTENTIONS = [
    "open the lunch warmly and welcome the intern into the conversation",
    "kick off with a casual hello and acknowledge the occasion",
    "greet the group and gesture the intern over",
]

_CLOSING_INTENTIONS = [
    "start wrapping up the lunch warmly — say you're heading back",
    "close with a 'good to have you' note to the intern",
    "wind down the chat and suggest getting back to work",
]


# --- Activation / arc planning --------------------------------------------


def activate(session_id: int) -> dict[str, Any] | None:
    """Transition a session to 'active' and plan its conversation arc.

    Idempotent: if the session is already active or completed, returns
    the current state without re-planning. Returns the session dict.
    """
    session = get_lunchroom_session(session_id)
    if not session:
        return None
    if session["status"] in ("active", "completed"):
        return session
    if session["status"] != "accepted":
        return None  # invited/declined/missed/cancelled can't activate

    participants = session.get("participants") or []
    if not participants:
        return None

    occasion = session.get("occasion") or "routine_lunch"
    occasion_detail = session.get("occasion_detail")

    beats = _plan_arc(
        participants=participants,
        occasion=occasion,
        occasion_detail=occasion_detail,
    )
    if not beats:
        return None

    # Persist beats as pending posts with staggered deliver_at offsets.
    now = scheduling.now_utc()
    offset_seconds = scheduling.LUNCHROOM_OPENING_DELAY_SECONDS
    seq = 1
    for beat in beats:
        deliver_at = scheduling.to_iso(now + timedelta(seconds=offset_seconds))
        create_lunchroom_post(
            session_id=session_id,
            sequence=seq,
            author_kind="character",
            author_slug=beat["slug"],
            author_name=beat["name"],
            intention=beat["intention"],
            deliver_at=deliver_at,
            status="pending",
        )
        seq += 1
        offset_seconds += _jittered_interval()

    mark_lunchroom_active(session_id)
    return get_lunchroom_session(session_id)


def _jittered_interval() -> int:
    base = scheduling.LUNCHROOM_BEAT_INTERVAL_SECONDS
    jitter = scheduling.LUNCHROOM_BEAT_JITTER_SECONDS
    if jitter <= 0:
        return base
    return max(1, base + random.randint(-jitter, jitter))  # noqa: S311


def _plan_arc(
    participants: list[dict[str, Any]],
    occasion: str,
    occasion_detail: str | None,
) -> list[dict[str, Any]]:
    """Produce an interleaved list of beats across all participants.

    Each beat: {slug, name, intention}. Opening beat goes to participant
    0 with an opening intention; closing beat goes to a random participant
    with a closing intention. Middle beats are interleaved round-robin
    across all participants, pulling from the generic pool with light
    contextual flavouring.
    """
    if not participants:
        return []

    provider = os.environ.get("LLM_PROVIDER", "stub").lower()
    if provider != "stub":
        # An LLM-based planner could produce a richer arc here. For now
        # the stub-based planner is plenty — the real texture comes from
        # the per-beat rendering call later.
        pass

    beats_per_char = random.randint(  # noqa: S311
        scheduling.LUNCHROOM_BEATS_PER_CHAR_MIN,
        scheduling.LUNCHROOM_BEATS_PER_CHAR_MAX,
    )

    # Build a per-character stack of intentions
    stacks: list[list[str]] = []
    for i, _p in enumerate(participants):
        pool = list(_GENERIC_INTENTIONS)
        random.shuffle(pool)  # noqa: S311
        picks = pool[:beats_per_char]
        if i == 0:
            picks[0] = random.choice(_OPENING_INTENTIONS)  # noqa: S311
        stacks.append(picks)

    # Optionally mention the occasion detail in one beat
    if occasion_detail and occasion != "routine_lunch":
        # Inject into the first participant's second beat if present
        if len(stacks[0]) > 1:
            stacks[0][1] = (
                f"naturally reference the context — {occasion_detail}"
            )

    # Interleave round-robin until all stacks are empty
    beats: list[dict[str, Any]] = []
    while any(stacks):
        for i, stack in enumerate(stacks):
            if not stack:
                continue
            intention = stack.pop(0)
            beats.append({
                "slug": participants[i]["slug"],
                "name": participants[i].get("name", participants[i]["slug"]),
                "intention": intention,
            })

    # Replace last beat with a closing intention from a random participant
    if beats:
        closer_idx = random.randrange(len(participants))  # noqa: S311
        beats[-1] = {
            "slug": participants[closer_idx]["slug"],
            "name": participants[closer_idx].get(
                "name", participants[closer_idx]["slug"],
            ),
            "intention": random.choice(_CLOSING_INTENTIONS),  # noqa: S311
        }

    # Clip to hard cap
    hard_cap = scheduling.LUNCHROOM_HARD_CAP
    if len(beats) > hard_cap:
        beats = beats[:hard_cap]
    return beats


# --- Delivery / rendering -------------------------------------------------


async def deliver_due(session_id: int) -> int:
    """Render content for any pending posts whose deliver_at has passed.

    Returns the number of posts delivered this call. Safe to call on
    every poll.
    """
    session = get_lunchroom_session(session_id)
    if not session or session["status"] != "active":
        return 0

    due = list_due_pending_posts(session_id)
    if not due:
        await _maybe_complete(session_id)
        return 0

    company_slug = _company_slug_for_session(session)
    company_name = _company_name_for_session(session)
    occasion = session.get("occasion") or "routine_lunch"
    occasion_detail = session.get("occasion_detail")

    transcript = _transcript_for_context(session_id)

    delivered = 0
    for post in due:
        # Refresh transcript after each delivery so later beats see earlier
        # ones in the same batch.
        text = await _render_beat(
            company_slug=company_slug,
            company_name=company_name,
            character_slug=post["author_slug"],
            character_name=post["author_name"] or "",
            intention=post["intention"] or "",
            occasion=occasion,
            occasion_detail=occasion_detail,
            transcript=transcript,
            turn=len(transcript) + 1,
        )
        mark_post_delivered(post["id"], text)
        transcript.append({
            "author": post["author_name"] or post["author_slug"] or "colleague",
            "text": text,
        })
        delivered += 1

    await _maybe_complete(session_id)
    return delivered


async def _maybe_complete(session_id: int) -> None:
    """Complete the session if the hard cap is hit or no pending beats remain.

    On the active→completed transition runs the Stage 5c end-of-session
    review (participation_notes + system_feedback) and drops the
    supportive system message in the student's work inbox.
    """
    session = get_lunchroom_session(session_id)
    if not session or session["status"] != "active":
        return
    delivered = count_delivered_posts(session_id)
    all_posts = list_lunchroom_posts(session_id)
    any_pending = any(p["status"] == "pending" for p in all_posts)
    if not (delivered >= scheduling.LUNCHROOM_HARD_CAP or not any_pending):
        return

    # Run Stage 5c review BEFORE marking completed so a review failure
    # doesn't strand the session — fall back to a neutral note + warm
    # default feedback if the LLM call breaks.
    try:
        notes, feedback = await _run_review(session)
    except Exception:  # noqa: BLE001
        notes, feedback = (
            "Lunch wrapped up — review unavailable.",
            "Hope you enjoyed the chat! Catch the team again next time.",
        )

    mark_lunchroom_completed(
        session_id,
        participation_notes=notes,
        system_feedback=feedback,
    )

    # Drop the warm supportive message in the work inbox so the student
    # sees it next time they check. Use a system sender — this is the
    # simulation talking, not a colleague.
    _deliver_system_feedback(session, feedback)


async def _render_beat(
    *,
    company_slug: str,
    company_name: str,
    character_slug: str | None,
    character_name: str,
    intention: str,
    occasion: str,
    occasion_detail: str | None,
    transcript: list[dict[str, str]],
    turn: int,
) -> str:
    """LLM call: turn an intention into a single line of in-character chat."""
    persona = _load_persona(company_slug, character_slug) if character_slug else ""

    occasion_blurb = (
        f"Occasion: {occasion}"
        + (f" — {occasion_detail}" if occasion_detail else "")
    )

    winding_down = turn >= scheduling.LUNCHROOM_SOFT_CAP
    wind_note = (
        "\n\nThe conversation is winding down — keep your message short "
        "and naturally start steering toward a warm close."
        if winding_down else ""
    )

    system_prompt = (
        f"{persona}\n\n"
        f"═══════════════════════════════════════════════════\n"
        f"You are {character_name}, having lunch with colleagues and a "
        f"new intern at {company_name}. This is a casual group chat — "
        f"think workplace Slack lunch channel, warm and short.\n\n"
        f"{occasion_blurb}\n\n"
        f"Write ONE short chat message (1-2 sentences, max ~40 words). "
        f"Stay fully in character. Speak in first person. Do not use "
        f"stage directions or emotes. Do not prefix with your name. Do "
        f"not mention you're an AI or that this is a simulation.\n\n"
        f"Your intention for this message: {intention}{wind_note}"
    )

    transcript_tail = transcript[-10:]
    context_lines = "\n".join(
        f"{m['author']}: {m['text']}" for m in transcript_tail
    )
    user_prompt = (
        f"Chat so far:\n{context_lines}\n\n"
        f"Write your next message now."
        if context_lines
        else "The chat is just starting. Write your opening message now."
    )

    # The shared chat_completion stub is tuned for interview replies, so
    # fall back to our lunchroom-specific stub when no real provider is set.
    if os.environ.get("LLM_PROVIDER", "stub").lower() == "stub":
        return _stub_render(intention, character_name, turn)

    try:
        raw = await chat_completion(
            system_prompt=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
    except Exception:  # noqa: BLE001
        raw = _stub_render(intention, character_name, turn)

    return _clean_line(raw) or _stub_render(intention, character_name, turn)


def _clean_line(text: str) -> str:
    """Strip name prefixes, quotes, and excess whitespace."""
    text = (text or "").strip()
    # Strip leading name: patterns like "Karen: hi there" or "**Karen**: ..."
    text = re.sub(r"^\*{0,2}[\w\s\-']{1,40}\*{0,2}:\s*", "", text)
    text = text.strip().strip('"').strip("'").strip()
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text)
    return text[:400]


def _stub_render(intention: str, character_name: str, turn: int) -> str:
    """Deterministic fallback line when no LLM is available."""
    first = character_name.split()[0] if character_name else "colleague"
    if "open" in intention or turn <= 1:
        return f"Hey! Grab a seat — glad you could make it."
    if "wrap" in intention or "close" in intention or "wind" in intention:
        return "Right, I should head back — good chatting with you!"
    if "ask the intern" in intention:
        return "So how's your week been going so far?"
    if "joke" in intention:
        return "The coffee machine's been holding a grudge against me all week."
    if "observation" in intention or "working on" in intention:
        return "Busy morning — deadlines everywhere, but we'll get there."
    if "react" in intention:
        return "Ha, same here — that's exactly what I was thinking."
    return f"Good to have you at the table with us."


# --- Transcript / context helpers -----------------------------------------


def _transcript_for_context(session_id: int) -> list[dict[str, str]]:
    """Return delivered posts as [{author, text}, ...] in order."""
    posts = list_lunchroom_posts(session_id, only_delivered=True)
    return [
        {
            "author": (p.get("author_name")
                       or p.get("author_slug")
                       or "student"),
            "text": p.get("content") or "",
        }
        for p in posts
    ]


def _company_slug_for_session(session: dict[str, Any]) -> str:
    from workready_api.db import get_application
    app_data = get_application(session["application_id"]) or {}
    return app_data.get("company_slug", "")


def _company_name_for_session(session: dict[str, Any]) -> str:
    from workready_api.db import get_application
    app_data = get_application(session["application_id"]) or {}
    slug = app_data.get("company_slug", "")
    job = get_job(slug, app_data.get("job_slug", "")) or {}
    return job.get("company") or (get_company(slug) or {}).get("company") or slug


_PERSONA_CACHE: dict[tuple[str, str], str] = {}


def _load_persona(company_slug: str, character_slug: str) -> str:
    """Load <SITES_DIR>/<company>/content/employees/<slug>-prompt.txt.

    Returns a short fallback if the file isn't available (e.g. in the
    container deployment where content files aren't shipped).
    """
    key = (company_slug, character_slug)
    if key in _PERSONA_CACHE:
        return _PERSONA_CACHE[key]

    sites_dir = Path(
        os.environ.get("SITES_DIR", str(Path(__file__).parent.parent.parent)),
    )
    candidate = (
        sites_dir / company_slug / "content" / "employees"
        / f"{character_slug}-prompt.txt"
    )
    if candidate.exists():
        try:
            text = candidate.read_text(encoding="utf-8")
        except OSError:
            text = ""
    else:
        text = ""

    if not text:
        text = (
            f"You are a colleague at {company_slug}. Warm, professional, "
            f"casual register — the kind of voice you'd use at a team lunch."
        )

    _PERSONA_CACHE[key] = text
    return text


# --- Student posting + @mention handling ----------------------------------


_MENTION_RE = re.compile(r"@([A-Za-z][A-Za-z0-9_\-]{1,40})")


def post_student_message(
    session_id: int, text: str, student_name: str | None = None,
) -> dict[str, Any] | None:
    """Insert a delivered student row and handle @mention rescheduling.

    Returns the new post row, or None if the session isn't active.
    """
    session = get_lunchroom_session(session_id)
    if not session or session["status"] != "active":
        return None

    text = (text or "").strip()
    if not text:
        return None
    text = text[:1000]

    participants = session.get("participants") or []
    mentions = _detect_mentions(text, participants)

    seq = next_post_sequence(session_id)
    now_iso = scheduling.to_iso(scheduling.now_utc())
    post_id = create_lunchroom_post(
        session_id=session_id,
        sequence=seq,
        author_kind="student",
        author_name=student_name or "You",
        deliver_at=now_iso,
        content=text,
        status="delivered",
        mentions=mentions,
    )

    # @mention: pull each mentioned character's next pending beat forward
    for slug in mentions:
        _reschedule_character_forward(session_id, slug)

    return {
        "id": post_id,
        "session_id": session_id,
        "sequence": seq,
        "author_kind": "student",
        "author_name": student_name or "You",
        "content": text,
        "deliver_at": now_iso,
        "status": "delivered",
        "mentions": mentions,
    }


def _detect_mentions(
    text: str, participants: list[dict[str, Any]],
) -> list[str]:
    """Find @mentions in the student's text. Match against first name OR slug.

    Returns character slugs (deduplicated, order-preserved).
    """
    if not text or not participants:
        return []

    raw_mentions = [m.lower() for m in _MENTION_RE.findall(text)]
    if not raw_mentions:
        return []

    found: list[str] = []
    for p in participants:
        slug = p.get("slug", "").lower()
        name = (p.get("name") or "").lower()
        first = name.split()[0] if name else ""
        keys = {k for k in (slug, first, name.replace(" ", "-")) if k}
        if any(rm in keys for rm in raw_mentions) and p["slug"] not in found:
            found.append(p["slug"])
    return found


def _reschedule_character_forward(session_id: int, character_slug: str) -> None:
    """Pull a character's next pending beat to the mention-response window."""
    beat = next_pending_post_for_character(session_id, character_slug)
    if not beat:
        return
    target = scheduling.now_utc() + timedelta(
        seconds=scheduling.LUNCHROOM_MENTION_RESCHEDULE_SECONDS,
    )
    target_iso = scheduling.to_iso(target)
    # Only pull forward — never push a beat later than already planned
    if target_iso < beat["deliver_at"]:
        update_post_deliver_at(beat["id"], target_iso)


# --- Stage 5c: end-of-session review ---------------------------------------

_REVIEW_SYSTEM_PROMPT = """You are a supportive observer reviewing a casual workplace lunch chat between a student intern and 2-3 colleagues. The student is in an educational simulation — they are learning how informal workplace social moments work, not being graded. Your job is to produce two pieces of output:

1. participation_notes — a short, factual, private note (3-5 sentences) about how the student showed up. Focus on observable behaviour: did they speak, ask questions, react to others, reference the work or the company, stay professional. NO scoring, NO grading. This note feeds into a later exit interview as context, so it should be useful to a friendly HR character — not a judgement.

2. system_feedback — a warm, supportive message FROM the simulation TO the student (2-4 sentences). NEVER shame the student. If they barely spoke, gently acknowledge that group lunches can be hard and offer one or two alternative ways to get to know the team (one-on-one chats, asking a mentor for a coffee). If they engaged well, celebrate that warmly. Always end on an encouraging note. Address the student in second person ("you").

Return ONLY a JSON object with exactly these two keys, no markdown fences, no commentary:
{"participation_notes": "...", "system_feedback": "..."}"""


async def _run_review(
    session: dict[str, Any],
) -> tuple[str, str]:
    """Run the Stage 5c review LLM call. Returns (notes, feedback)."""
    rows = list_lunchroom_posts(session["id"], only_delivered=True)
    transcript = [
        {
            "author": r.get("author_name")
            or r.get("author_slug")
            or "colleague",
            "text": r.get("content") or "",
            "is_student": r.get("author_kind") == "student",
        }
        for r in rows
    ]
    student_turns = [m for m in transcript if m["is_student"]]

    if os.environ.get("LLM_PROVIDER", "stub").lower() == "stub":
        return _stub_review(len(student_turns), len(transcript))

    company_name = _company_name_for_session(session)
    occasion = session.get("occasion") or "routine_lunch"

    transcript_text = "\n".join(
        f"{'STUDENT' if m['is_student'] else m['author']}: {m['text']}"
        for m in transcript
    )
    user_prompt = (
        f"Company: {company_name}\n"
        f"Occasion: {occasion}\n"
        f"Total messages: {len(transcript)}\n"
        f"Student messages: {len(student_turns)}\n\n"
        f"Full transcript:\n\n{transcript_text}\n\n"
        f"Produce the JSON now."
    )

    raw = await chat_completion(
        system_prompt=_REVIEW_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )
    return _parse_review(raw, fallback_count=len(student_turns),
                         total=len(transcript))


def _stub_review(
    student_turn_count: int, total: int,
) -> tuple[str, str]:
    """Deterministic 5c review for dev — three tiers based on engagement."""
    n = student_turn_count
    if n == 0:
        notes = (
            "Student stayed quiet through the lunch — didn't post any "
            "messages. They were present but not participating in the "
            "conversation."
        )
        feedback = (
            "Hey, thanks for joining the lunch. Group chats can be a lot, "
            "especially with people you don't know yet — that's completely "
            "normal. If you'd like to get to know the team in a different "
            "way, try asking your mentor for a quick coffee, or DM one of "
            "the colleagues you found interesting. The team likes having "
            "you around."
        )
    elif n <= 2:
        notes = (
            f"Student posted {n} message(s) during the lunch. They "
            f"engaged briefly but mostly listened. Polite and professional "
            f"throughout."
        )
        feedback = (
            "Nice job dropping into the lunch! It can take a few times to "
            "feel comfortable jumping in — you did well. Next time, try "
            "asking one of the colleagues a question about what they're "
            "working on. Small things go a long way."
        )
    else:
        notes = (
            f"Student posted {n} messages across {total} total. Engaged "
            f"actively, asked or responded naturally, and held their own "
            f"in the conversation. Good professional warmth."
        )
        feedback = (
            "That was great — you really joined in and the team enjoyed "
            "having you at the table. Keep showing up like that; this is "
            "exactly the kind of informal connection that makes a "
            "placement memorable."
        )
    return notes, feedback


def _parse_review(
    raw: str, *, fallback_count: int, total: int,
) -> tuple[str, str]:
    """Strip ```json fences and parse, falling back to stub on error."""
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
        notes = str(data.get("participation_notes") or "").strip()
        feedback = str(data.get("system_feedback") or "").strip()
        if notes and feedback:
            return notes, feedback
    except (ValueError, TypeError):
        pass

    return _stub_review(fallback_count, total)


def _deliver_system_feedback(session: dict[str, Any], feedback: str) -> None:
    """Drop the supportive message in the student's work inbox.

    Sender is the simulation itself (a system sender), not a character —
    bounces if replied to. Marked with related_stage='mid_placement' so the
    portal can style or filter it later.
    """
    application_id = session["application_id"]
    app_data = get_application(application_id) or {}
    student = get_student_by_id(app_data.get("student_id", 0)) or {}
    if not student:
        return

    company_slug = app_data.get("company_slug", "")
    company_name = (
        (get_job(company_slug, app_data.get("job_slug", "")) or {}).get("company")
        or (get_company(company_slug) or {}).get("company")
        or company_slug
    )

    create_message(
        student_id=student["id"],
        student_email=student.get("email", ""),
        sender_name="WorkReady",
        sender_role="Simulation guide",
        sender_email="noreply@workready.eduserver.au",
        subject=f"After the lunch — {company_name}",
        body=feedback,
        inbox="work",
        application_id=application_id,
        related_stage="mid_placement",
    )

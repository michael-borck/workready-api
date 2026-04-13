"""FastAPI application for the WorkReady Simulation API."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from workready_api.assessor import assess
from workready_api.blocking import get_blocked_for_student
from workready_api.db import (
    advance_stage,
    append_interview_message,
    cancel_task_deadline_event,
    complete_interview_session,
    create_application,
    create_booking,
    create_interview_session,
    create_task_submission,
    delete_pending_messages_for_booking,
    get_active_booking,
    get_all_postings,
    get_application,
    get_bookings_for_application,
    get_calendar_event,
    get_db,
    get_inbox,
    get_interview_session,
    get_latest_submission,
    get_lunchroom_session,
    get_or_create_student,
    get_posting,
    get_stage_results,
    get_student_applications,
    get_student_by_email,
    get_task,
    increment_missed_interviews,
    increment_reschedule_count,
    init_db,
    list_calendar_events,
    list_lunchroom_sessions_for_application,
    list_prior_task_history,
    list_tasks_for_application,
    mark_message_read,
    mark_task_reviewed,
    mark_task_submitted,
    record_stage_result,
    set_application_status,
    update_booking_status,
    update_calendar_event_status,
)
from workready_api import lunchroom as lunchroom_mod
from workready_api.interview import (
    TARGET_TURNS,
    WRAP_UP_AFTER,
    assess_interview,
    build_interview_system_prompt,
    chat_completion,
)
from workready_api import scheduling
from workready_api.jobs import (
    get_company_business_hours,
    get_job,
    get_job_description,
    load_jobs,
    seed_postings_from_jobs,
    seed_task_templates_from_jobs,
)
from workready_api.placement import (
    activate_work_placement,
    reveal_next_task_after_submission,
)
from workready_api.task_reviewer import review_task_submission
from workready_api.notifications import NotifyContent, notify
from workready_api.models import (
    ApplicationDetail,
    ApplicationSummary,
    AssessmentResult,
    BlockedJob,
    BookingRequest,
    BookingState,
    Inbox,
    InterviewBooking,
    InterviewMessage,
    InterviewMessageReply,
    InterviewMessageRequest,
    InterviewSession,
    InterviewStartRequest,
    Message,
    PostingList,
    PublicPosting,
    SlotOption,
    SlotOptions,
    StageResult,
    StudentProgress,
    StudentState,
    CalendarEvent,
    CalendarEventList,
    LunchroomParticipant,
    LunchroomSession,
    LunchroomSessionList,
    LunchroomSlot,
    LunchroomSlotPickRequest,
    TaskDetail,
    TaskFeedback,
    TaskList,
    TaskSubmitResult,
    TaskSummary,
)
from workready_api.pdf import extract_text

SITE_SLUGS = [
    "nexuspoint-systems",
    "ironvale-resources",
    "meridian-advisory",
    "metro-council-wa",
    "southern-cross-financial",
    "horizon-foundation",
]

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Initialise database and load job data on startup."""
    init_db()
    sites_dir = Path(os.environ.get("SITES_DIR", str(Path(__file__).parent.parent.parent)))
    load_jobs(sites_dir, SITE_SLUGS)
    # Auto-seed postings from loaded jobs (idempotent — safe on every startup)
    seed_postings_from_jobs()
    # Auto-seed task templates from loaded jobs (idempotent — Stage 4)
    seed_task_templates_from_jobs()
    # Build the email address registry from loaded employee/company data
    from workready_api.email_registry import build_registry
    registry = build_registry()
    import logging
    logging.getLogger(__name__).info("Email registry: %d valid addresses", len(registry))
    yield


app = FastAPI(
    title="WorkReady Simulation API",
    version="0.2.0",
    description=(
        "Backend for the WorkReady internship simulation. "
        "Tracks student progress through 6 stages: job board, resume, "
        "interview, work task, lunchroom moment, exit interview."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://michael-borck.github.io",
        "http://localhost:8080",
        "http://127.0.0.1:8080",
        "http://localhost:3000",
    ],
    # Allow any *.eduserver.au subdomain (workready, company sites, etc.)
    allow_origin_regex=r"https://([a-z0-9-]+\.)*eduserver\.au",
    allow_methods=["*"],
    allow_headers=["*"],
)


# Mount admin / debug router (gated by WORKREADY_ADMIN_TOKEN)
from workready_api.admin import router as admin_router  # noqa: E402
app.include_router(admin_router)

# Mount in-app mail router (compose, send, reply, delete, sent box)
from workready_api.mail import router as mail_router  # noqa: E402
app.include_router(mail_router)


# --- Helpers ---


def _format_bullets(items: list[str], indent: str = "  • ") -> str:
    """Format a list as a bullet list, returning '(none)' for empty."""
    if not items:
        return f"{indent}(none)"
    return "\n".join(f"{indent}{item}" for item in items)


SIMULATION_NOTE_HEADER = (
    "════════════════════════════════════════════════════════════\n"
    "  WORKREADY SIMULATION NOTE — FOR YOUR LEARNING\n"
    "════════════════════════════════════════════════════════════\n"
    "This level of feedback isn't typically shared by real employers.\n"
    "We've included it here so you can learn from this experience and\n"
    "use the insights when applying for real roles.\n"
    "────────────────────────────────────────────────────────────"
)


def _format_resume_feedback(feedback: dict, fit_score: int) -> str:
    """Format the resume assessment feedback as a plain-text block."""
    return (
        f"{SIMULATION_NOTE_HEADER}\n\n"
        f"YOUR APPLICATION SUMMARY\n"
        f"Overall fit score: {fit_score}/100\n\n"
        f"WHAT WORKED WELL\n"
        f"{_format_bullets(feedback.get('strengths', []))}\n\n"
        f"AREAS FOR IMPROVEMENT\n"
        f"{_format_bullets(feedback.get('gaps', []))}\n\n"
        f"SUGGESTIONS\n"
        f"{_format_bullets(feedback.get('suggestions', []))}\n\n"
        f"TAILORING ASSESSMENT\n"
        f"  {feedback.get('tailoring', '(none)')}\n\n"
        f"────────────────────────────────────────────────────────────\n"
        f"Want to practise for similar roles? Download a practice script\n"
        f"from the WorkReady portal and use it with Talk Buddy or any AI\n"
        f"chat tool to rehearse before your next attempt."
    )


def _format_interview_feedback(feedback: dict, fit_score: int) -> str:
    """Format the interview assessment feedback as a plain-text block."""
    return (
        f"{SIMULATION_NOTE_HEADER}\n\n"
        f"INTERVIEW SUMMARY\n"
        f"Overall score: {fit_score}/100\n\n"
        f"WHAT WORKED WELL\n"
        f"{_format_bullets(feedback.get('strengths', []))}\n\n"
        f"AREAS FOR IMPROVEMENT\n"
        f"{_format_bullets(feedback.get('gaps', []))}\n\n"
        f"SUGGESTIONS\n"
        f"{_format_bullets(feedback.get('suggestions', []))}\n\n"
        f"────────────────────────────────────────────────────────────\n"
        f"Want to practise interviews? Download a practice script for\n"
        f"this role from your WorkReady portal and use it with Talk Buddy\n"
        f"or any AI chat tool to rehearse before your next attempt."
    )


def _revealed_postings_for_student(student_id: int) -> set[int]:
    """Postings (by id) the student has had a confidential reveal for.

    A confidential posting is "revealed" once the student passes the
    resume stage on it (interview invitation reveals the company).
    """
    with get_db() as conn:
        rows = conn.execute(
            """SELECT DISTINCT posting_id FROM applications
               WHERE student_id = ? AND posting_id IS NOT NULL
                 AND current_stage != 'resume'""",
            (student_id,),
        ).fetchall()
    return {r["posting_id"] for r in rows if r["posting_id"]}


def _build_public_posting(
    posting: dict,
    revealed_ids: set[int] | None = None,
) -> PublicPosting:
    """Build the public-facing PublicPosting from a DB row + jobs.json data."""
    company_slug = posting["company_slug"]
    job_slug = posting["job_slug"]
    job = get_job(company_slug, job_slug) or {}

    confidential = bool(posting["confidential"])
    revealed = bool(revealed_ids and posting["id"] in revealed_ids)
    hide_company = confidential and not revealed

    if hide_company:
        return PublicPosting(
            id=posting["id"],
            source_type=posting["source_type"],
            agency_name=posting["agency_name"],
            listing_title=posting["listing_title"],
            listing_description=posting["listing_description"],
            confidential=True,
            # Company-revealing fields nulled out
            company_slug=None,
            company_name=None,
            company_url=None,
            job_slug=None,
            job_title=None,
            department=None,
            # Safe to expose general location and employment type
            location=job.get("location"),
            employment_type=job.get("employment_type"),
            apply_url=None,
        )

    return PublicPosting(
        id=posting["id"],
        source_type=posting["source_type"],
        agency_name=posting["agency_name"],
        listing_title=posting["listing_title"],
        listing_description=posting["listing_description"],
        confidential=confidential,
        company_slug=company_slug,
        company_name=job.get("company"),
        company_url=job.get("company_url"),
        job_slug=job_slug,
        job_title=job.get("title"),
        department=job.get("department"),
        location=job.get("location"),
        employment_type=job.get("employment_type"),
        # External apply URL only for direct postings
        apply_url=job.get("url") if posting["source_type"] == "direct" else None,
    )


# --- Health ---


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "version": "0.2.0"}


# --- Public job board (for seek.jobs) ---


@app.get("/api/v1/postings", response_model=PostingList)
def list_postings(email: str | None = None) -> PostingList:
    """List all postings for the public job board.

    If `email` is provided, the response respects which confidential
    postings the student has had a reveal for. Without email, all
    confidential postings are anonymised.
    """
    revealed_ids: set[int] = set()
    if email:
        student = get_student_by_email(email)
        if student:
            revealed_ids = _revealed_postings_for_student(student["id"])

    postings = get_all_postings()
    public = [_build_public_posting(p, revealed_ids) for p in postings]
    return PostingList(postings=public, total=len(public))


# --- Stage 2: Resume submission ---


@app.post("/api/v1/resume", response_model=AssessmentResult)
async def submit_resume(
    company_slug: str = Form(""),
    job_slug: str = Form(""),
    job_title: str = Form(...),
    applicant_name: str = Form(...),
    applicant_email: str = Form(...),
    cover_letter: str = Form(""),
    source: str = Form("direct"),
    posting_id: int | None = Form(None),
    resume: UploadFile = File(...),
) -> AssessmentResult:
    """Stage 2 — Submit a resume for assessment.

    Creates a student record (if new), creates an application,
    assesses the resume, and records the result.

    If posting_id is provided, the company_slug/job_slug are resolved
    from the posting (so confidential agency listings don't expose the
    real company in the form payload). If not provided, falls back to
    the company_slug/job_slug params (legacy direct apply forms).
    """
    # Resolve posting if specified
    posting = None
    if posting_id is not None:
        posting = get_posting(posting_id)
        if posting:
            company_slug = posting["company_slug"]
            job_slug = posting["job_slug"]

    if not company_slug or not job_slug:
        raise HTTPException(
            status_code=400,
            detail="Either posting_id or (company_slug, job_slug) is required",
        )

    # Extract text from uploaded PDF
    pdf_bytes = await resume.read()
    resume_text = extract_text(pdf_bytes)

    # Look up the job description for comparison
    job_description = get_job_description(company_slug, job_slug)

    # Assess using configured provider (stub, ollama, anthropic, openrouter)
    result = await assess(
        resume_text=resume_text,
        cover_letter=cover_letter,
        job_title=job_title,
        job_description=job_description,
    )

    # Persist student, application, and stage result
    student = get_or_create_student(applicant_email, applicant_name)

    # Determine the effective source from the posting type
    effective_source = source
    if posting:
        effective_source = "agency" if posting["source_type"] == "agency" else source

    application_id = create_application(
        student_id=student["id"],
        student_email=applicant_email,
        company_slug=company_slug,
        job_slug=job_slug,
        job_title=job_title,
        source=effective_source,
        posting_id=posting_id,
    )

    record_stage_result(
        application_id=application_id,
        stage="resume",
        status="passed" if result.proceed_to_interview else "failed",
        score=result.fit_score,
        feedback=result.feedback.model_dump(),
    )

    # Determine display title and sender for the confirmation message.
    # Confidential agency listings keep the company hidden until interview.
    is_confidential = bool(posting and posting.get("confidential"))
    is_agency = bool(posting and posting["source_type"] == "agency")
    agency_name = posting["agency_name"] if is_agency else None
    listing_title = posting["listing_title"] if posting else job_title

    # Confirmation notification (immediate)
    confirmation_sender = (
        f"{agency_name}" if is_agency else "seek.jobs"
    )
    confirmation_via = f" (via {agency_name})" if is_agency else ""
    notify(
        student_email=applicant_email,
        event="application_received",
        content=NotifyContent(
            sender_name=confirmation_sender,
            sender_role="Recruitment" if is_agency else "Application System",
            subject=f"Application received — {listing_title}",
            body=(
                f"Hi {applicant_name},\n\n"
                f"Thank you for applying for the {listing_title} position{confirmation_via}. "
                f"We have received your application and it is now under review. "
                f"You will hear back from us shortly.\n\n"
                f"— {confirmation_sender}"
            ),
            application_id=application_id,
            related_stage="resume",
        ),
    )

    # Personal inbox: outcome message with full feedback inline
    job_meta = get_job(company_slug, job_slug)
    company_name = job_meta["company"] if job_meta else company_slug
    feedback_dict = result.feedback.model_dump()
    feedback_block = _format_resume_feedback(feedback_dict, result.fit_score)

    # Compute delivery time for the outcome message — by default this is
    # immediate, but if RESUME_FEEDBACK_DELAY_MINUTES is set the message
    # is hidden in the inbox until enough time passes (lazy evaluation).
    outcome_deliver_at = scheduling.feedback_delivery_time(
        scheduling.RESUME_FEEDBACK_DELAY_MINUTES,
        scheduling.RESUME_FEEDBACK_DELAY_JITTER_MINUTES,
    )

    if result.proceed_to_interview:
        advance_stage(application_id, "interview")
        # Interview invitation always reveals the actual company —
        # this is the dramatic reveal moment for confidential listings.
        reveal_intro = ""
        if is_confidential:
            reveal_intro = (
                f"We're delighted to share that the role you applied for is at "
                f"**{company_name}**. We can now disclose the full details of "
                f"the opportunity and the team you'd be joining.\n\n"
            )
        notify(
            student_email=applicant_email,
            event="interview_invitation",
            content=NotifyContent(
                sender_name=f"{company_name} HR",
                sender_role="Recruitment Team",
                subject=f"Interview invitation — {job_title} at {company_name}",
                body=(
                    f"Dear {applicant_name},\n\n"
                    f"{reveal_intro}"
                    f"Thank you for your application for the {job_title} role at "
                    f"{company_name}. We were impressed by your application and "
                    f"would like to invite you to an interview.\n\n"
                    f"You'll find the interview ready in your WorkReady portal "
                    f"under your dashboard. The interview will be a conversation "
                    f"with the hiring manager and should take around 15 minutes.\n\n"
                    f"We look forward to meeting you.\n\n"
                    f"Best regards,\n"
                    f"{company_name} Recruitment\n\n"
                    f"\n{feedback_block}"
                ),
                application_id=application_id,
                related_stage="interview",
                deliver_at=outcome_deliver_at,
            ),
        )
    else:
        # Mark application as rejected so the company is "off the board"
        set_application_status(application_id, "rejected")
        # Confidential listings keep the company hidden even on rejection —
        # the student never finds out who they applied to (realistic for
        # agency-mediated rejections)
        reject_sender = agency_name if is_confidential else f"{company_name} HR"
        reject_role = "Recruitment" if is_confidential else "Recruitment Team"
        reject_subject = (
            f"Update on your application — {listing_title}"
            if is_confidential
            else f"Update on your application — {job_title}"
        )
        reject_about = (
            f"the {listing_title} position we were recruiting for"
            if is_confidential
            else f"the {job_title} role at {company_name}"
        )
        reject_signoff = agency_name if is_confidential else f"{company_name} Recruitment"
        notify(
            student_email=applicant_email,
            event="application_rejected",
            content=NotifyContent(
                sender_name=reject_sender,
                sender_role=reject_role,
                subject=reject_subject,
                body=(
                    f"Dear {applicant_name},\n\n"
                    f"Thank you for your interest in {reject_about} "
                    f"and for taking the time to submit your application.\n\n"
                    f"After careful consideration, we have decided not to "
                    f"progress your application at this time. We had a strong "
                    f"field of applicants and the decision was a difficult one.\n\n"
                    f"We wish you the best in your career and encourage you to "
                    f"apply for other roles that may be a better fit.\n\n"
                    f"Best regards,\n"
                    f"{reject_signoff}\n\n"
                    f"\n{feedback_block}"
                ),
                application_id=application_id,
                related_stage="resume",
                deliver_at=outcome_deliver_at,
            ),
        )

    result.application_id = application_id
    return result


# --- Student progress ---


@app.get("/api/v1/student/{email}", response_model=StudentProgress)
def get_student_progress(email: str) -> StudentProgress:
    """Get all applications and progress for a student."""
    student = get_student_by_email(email)
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    applications = get_student_applications(student["id"])
    if not applications:
        raise HTTPException(status_code=404, detail="Student has no applications")

    return StudentProgress(
        email=email,
        name=student["name"],
        applications=[
            ApplicationSummary(**{k: v for k, v in a.items() if k not in ("student_email", "student_id")})
            for a in applications
        ],
    )


def _name_from_email(email: str) -> str:
    """Derive a friendly name from an email address.

    firstname.lastname@curtin.edu.au → Firstname Lastname
    jdoe@curtin.edu.au → Jdoe
    """
    local = email.split("@")[0]
    parts = local.replace("_", ".").replace("-", ".").split(".")
    return " ".join(p.capitalize() for p in parts if p)


def _send_welcome_email(email: str, name: str) -> None:
    """Send the welcome notification to a newly registered student."""
    notify(
        student_email=email,
        event="welcome",
        content=NotifyContent(
            sender_name="WorkReady Team",
            sender_role="Curtin University",
            subject="Welcome to WorkReady — Your Internship Journey Starts Here",
            body=(
                f"Hi {name},\n\n"
                f"Welcome to WorkReady — a simulated internship experience where "
                f"you can practise the full arc of a real placement, from finding "
                f"a job through to your exit interview.\n\n"
                f"This is a safe space to make mistakes and learn from them. "
                f"Nothing you do here affects your real career.\n\n"
                f"HOW TO GET STARTED\n\n"
                f"1. Play the Primer (optional but recommended)\n"
                f"   A short interactive story that walks you through the six "
                f"   stages of an internship. About 15 minutes. You can play it "
                f"   multiple times to explore different paths.\n\n"
                f"2. Browse seek.jobs\n"
                f"   Our job board lists internships and graduate roles across "
                f"   six fictional Western Australian companies. Find one that "
                f"   interests you and read the job description carefully.\n\n"
                f"3. Apply for a role\n"
                f"   When you find a job that fits, submit your resume on the "
                f"   company's careers page. You'll get feedback on how well "
                f"   your application matched the role.\n\n"
                f"4. Watch this inbox\n"
                f"   You'll receive updates here as your applications progress.\n\n"
                f"WHAT TO EXPECT\n\n"
                f"WorkReady is designed to feel real. You may not get the first "
                f"job you apply for. Feedback might sting. That's the point — "
                f"you'll be much better prepared when it counts.\n\n"
                f"Good luck.\n\n"
                f"— The WorkReady Team\n"
                f"Curtin University"
            ),
        ),
    )


@app.get("/api/v1/student/{email}/state", response_model=StudentState)
def get_student_state(email: str) -> StudentState:
    """Get the high-level state of a student for the portal.

    On first lookup, creates the student record and sends a welcome email.
    Returns the state machine value (NOT_APPLIED, APPLIED, HIRED, COMPLETED),
    active application if any, and unread message counts.
    """
    student = get_student_by_email(email)

    # First-time sign-in: create student and send welcome email
    if not student:
        name = _name_from_email(email)
        student = get_or_create_student(email, name)
        _send_welcome_email(email, name)

    student_id = student["id"]
    applications = get_student_applications(student_id)

    # Determine state from the most recent ACTIVE application (if any).
    # Rejected applications don't drive state — they just block the company.
    state = "NOT_APPLIED"
    active = None
    active_apps = [a for a in applications if a.get("status", "active") == "active"]
    if active_apps:
        latest = active_apps[0]
        active = ApplicationSummary(
            **{k: v for k, v in latest.items() if k not in ("student_email", "student_id")}
        )
        stage = latest["current_stage"]
        if stage == "resume":
            state = "APPLIED"
        elif stage in ("interview", "work_task", "lunchroom", "exit_interview"):
            state = "HIRED"
        elif stage == "completed":
            state = "COMPLETED"

    # Count unread messages per inbox
    personal_msgs = get_inbox(student_id, "personal")
    work_msgs = get_inbox(student_id, "work")
    unread_personal = sum(1 for m in personal_msgs if not m.get("is_read"))
    unread_work = sum(1 for m in work_msgs if not m.get("is_read"))

    blocked = get_blocked_for_student(student_id)

    return StudentState(
        email=email,
        name=student["name"],
        state=state,
        active_application=active,
        applications=[
            ApplicationSummary(**{k: v for k, v in a.items() if k not in ("student_email", "student_id")})
            for a in applications
        ],
        unread_personal=unread_personal,
        unread_work=unread_work,
        blocked_companies=blocked["companies"],
        blocked_jobs=[BlockedJob(**j) for j in blocked["jobs"]],
    )


@app.get("/api/v1/inbox/{email}", response_model=Inbox)
def get_inbox_endpoint(email: str, inbox: str = "personal") -> Inbox:
    """Get a student's inbox messages."""
    student = get_student_by_email(email)
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")
    messages = get_inbox(student["id"], inbox)
    return Inbox(
        inbox=inbox,
        messages=[
            Message(**{
                k: v for k, v in {**m, "is_read": bool(m.get("is_read"))}.items()
                if k not in ("student_id", "student_email")
            })
            for m in messages
        ],
        unread_count=sum(1 for m in messages if not m.get("is_read")),
    )


@app.post("/api/v1/inbox/message/{message_id}/read")
def mark_read(message_id: int) -> dict:
    """Mark a message as read."""
    mark_message_read(message_id)
    return {"status": "ok"}


@app.get("/api/v1/application/{application_id}", response_model=ApplicationDetail)
def get_application_detail(application_id: int) -> ApplicationDetail:
    """Get full detail of an application including all stage results."""
    app_data = get_application(application_id)
    if not app_data:
        raise HTTPException(status_code=404, detail="Application not found")

    stages = get_stage_results(application_id)

    return ApplicationDetail(
        application=ApplicationSummary(
            **{k: v for k, v in app_data.items() if k not in ("student_email", "student_id")}
        ),
        stages=[
            StageResult(**s)
            for s in stages
        ],
    )


# --- Practice script (downloadable for Talk Buddy / any LLM tool) ---


def _build_practice_script(job: dict, posting: dict | None = None) -> str:
    """Generate a markdown practice script for an interview.

    Self-contained: contains the manager persona, job description, and
    instructions for using the script in Talk Buddy or any AI chat tool.
    Students download this and practise offline as many times as they like.
    """
    company_name = job.get("company", "")
    job_title = job.get("title", "")
    department = job.get("department", "")
    location = job.get("location", "Perth, Western Australia")
    employment_type = job.get("employment_type", "")
    manager_name = job.get("reports_to", "the hiring manager")
    manager_persona = job.get("manager_persona", "").strip()
    description = (job.get("description", "") or "").strip()

    return f"""# Interview Practice: {job_title}

**Company:** {company_name}
**Department:** {department}
**Location:** {location}
**Employment type:** {employment_type}
**Hiring manager:** {manager_name}

---

## How to use this script

This is a practice exercise — not the real WorkReady interview. Use it as
many times as you like to rehearse before applying or after a difficult
attempt. The full system prompt below configures any AI chat tool to
play the hiring manager.

### Option 1 — Talk Buddy (recommended)

1. Open Talk Buddy
2. Create a new scenario
3. Copy the **System Prompt** section below into the scenario configuration
4. Start the practice — you'll be the candidate, the AI will be {manager_name}
5. Talk Buddy supports voice, so you can practise speaking under pressure

### Option 2 — Any AI chat tool (ChatGPT, Claude, etc.)

1. Open the chat tool of your choice
2. Paste the **System Prompt** section below as your first message
3. Add: "Please stay in this character and conduct the interview"
4. Have a back-and-forth conversation as the candidate
5. At the end, ask: "Please give me detailed feedback on how I did"

---

## System Prompt

```
{manager_persona}

═══════════════════════════════════════════════════════════
You are conducting a job interview for the role of {job_title} at {company_name}.

This is a practice session. Stay in character throughout. Speak as you
would to a real candidate — warm but professional, curious, evaluating.

JOB DESCRIPTION:
{description[:2000]}

GUIDELINES:
- Speak conversationally, in first person, as the manager character
- Ask ONE question at a time and wait for the answer
- Cover these phases naturally over ~10 exchanges:
  1. Welcome & icebreaker
  2. Motivation & company research
  3. Role-specific questions
  4. Behavioural questions ("Tell me about a time when...")
  5. Candidate questions
  6. Close
- Be warm but don't fawn. Be honest but not harsh.
- After the interview ends, provide structured feedback covering:
  - Overall impression
  - Strengths
  - Areas for improvement
  - Specific suggestions to practise next time
```

---

## What to focus on while practising

- **Tell concrete stories.** Use the STAR method (Situation, Task, Action,
  Result) for behavioural questions.
- **Reference {company_name} specifically.** Show you've thought about
  why this company, not just any company in the sector.
- **Ask good questions.** Prepare 2–3 thoughtful questions about the role,
  the team, or the company's direction.
- **Pace yourself.** It's OK to pause before answering. Better to take a
  breath than to ramble.
- **Be authentic.** The manager will see through rehearsed answers.

## Self-evaluation

After each practice session, ask yourself:

- [ ] Did I give specific, concrete examples instead of generalities?
- [ ] Did I mention {company_name} or its work specifically?
- [ ] Did I ask thoughtful questions at the end?
- [ ] Did I handle moments of uncertainty without panicking?
- [ ] Could I tell a clearer story about why I want this role?

---

*Generated by the WorkReady simulation. This practice script is designed
to help you prepare for an interview at {company_name} or any similar
role. Practise as many times as you like — there's no limit and no
record kept.*
"""


@app.get("/api/v1/jobs/{company_slug}/{job_slug}/practice-script")
def get_practice_script(company_slug: str, job_slug: str) -> Response:
    """Return a markdown practice script for a job.

    The student downloads this and uses it in Talk Buddy or any AI chat
    tool to rehearse for the interview offline. The script contains the
    manager persona, job description, and practice instructions.
    """
    job = get_job(company_slug, job_slug)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    markdown = _build_practice_script(job)
    filename = f"practice-{company_slug}-{job_slug}.md"
    return Response(
        content=markdown,
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


# --- Interview booking ---


def _company_hours_config(company_slug: str) -> scheduling.BusinessHoursConfig:
    """Look up the business hours config for a company, with global fallback."""
    override = get_company_business_hours(company_slug)
    return scheduling.BusinessHoursConfig.from_dict(override)


def _format_business_hours_human(company_slug: str | None = None) -> str:
    """Human-readable business hours summary. Company-specific if slug given."""
    if company_slug:
        cfg = _company_hours_config(company_slug)
    else:
        cfg = scheduling.BusinessHoursConfig.global_default()
    return cfg.human_summary()


def _try_use_reschedule(application_id: int, app_data: dict) -> int:
    """Check the reschedule limit, increment if allowed, return new count.

    Raises HTTPException(400) if the application is at the hard limit.
    Returns the new reschedule count after incrementing.

    In soft mode, always increments and never raises.
    """
    current = app_data.get("reschedule_count", 0) or 0
    if (
        scheduling.RESCHEDULE_LIMIT_MODE == "hard"
        and current >= scheduling.MAX_RESCHEDULES
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                f"You have used your {scheduling.MAX_RESCHEDULES} allowed "
                f"reschedule(s) for this interview. The current booking is "
                f"final — please attend at the scheduled time."
            ),
        )
    return increment_reschedule_count(application_id)


def _create_reminders(
    booking_id: int,
    application_id: int,
    student_email: str,
    student_name: str,
    job_title: str,
    nice_time: str,
    sender_name: str,
    sender_role: str,
    is_confidential: bool,
    role_at_company: str,
    scheduled: "datetime",
) -> None:
    """Create future-dated reminder messages 24h and 1h before the booking.

    Reminders are tied to booking_id so they can be cancelled together
    with the booking. Skips reminders that would already be in the past
    (e.g. 24h reminder for a booking only 4 hours away).
    """
    now = scheduling.now_utc()
    twenty_four_h_before = scheduled - timedelta(hours=24)
    one_h_before = scheduled - timedelta(hours=1)

    if twenty_four_h_before > now:
        notify(
            student_email=student_email,
            event="interview_invitation",  # closest event for now
            content=NotifyContent(
                sender_name=sender_name,
                sender_role=sender_role,
                subject=f"Reminder — interview tomorrow ({nice_time})",
                body=(
                    f"Dear {student_name or 'Candidate'},\n\n"
                    f"This is a friendly reminder that your interview for "
                    f"{role_at_company} is tomorrow at:\n\n"
                    f"  {nice_time}\n\n"
                    f"A few things to prepare:\n"
                    f"  - Have your notes and any questions ready\n"
                    f"  - Test your internet connection in advance\n"
                    f"  - Find a quiet space where you won't be interrupted\n"
                    f"  - Plan to log in 5 minutes early\n\n"
                    f"Remember: if you're more than "
                    f"{scheduling.LATE_GRACE_MINUTES} minutes late, you'll "
                    f"need to reschedule.\n\n"
                    f"Good luck — we're looking forward to meeting you.\n\n"
                    f"Best regards,\n"
                    f"{sender_name}"
                ),
                application_id=application_id,
                booking_id=booking_id,
                related_stage="interview",
                deliver_at=scheduling.to_iso(twenty_four_h_before),
            ),
        )

    if one_h_before > now:
        notify(
            student_email=student_email,
            event="interview_invitation",
            content=NotifyContent(
                sender_name=sender_name,
                sender_role=sender_role,
                subject=f"Reminder — interview in 1 hour",
                body=(
                    f"Dear {student_name or 'Candidate'},\n\n"
                    f"Your interview for {role_at_company} starts in about "
                    f"one hour at {nice_time}.\n\n"
                    f"Please log into your WorkReady portal and head to the "
                    f"Interview view a few minutes before the scheduled "
                    f"start. Click 'Begin Interview' when you're ready.\n\n"
                    f"You've got this.\n\n"
                    f"Best regards,\n"
                    f"{sender_name}"
                ),
                application_id=application_id,
                booking_id=booking_id,
                related_stage="interview",
                deliver_at=scheduling.to_iso(one_h_before),
            ),
        )


def _build_booking_state(application_id: int, app_data: dict) -> BookingState:
    """Compute the current booking state for an application."""
    booking_row = get_active_booking(application_id)
    booking = None
    if booking_row:
        booking = InterviewBooking(
            id=booking_row["id"],
            application_id=booking_row["application_id"],
            scheduled_at=booking_row["scheduled_at"],
            status=booking_row["status"],
            created_at=booking_row["created_at"],
            completed_at=booking_row.get("completed_at"),
        )

    missed = app_data.get("missed_interviews", 0) or 0
    max_missed = scheduling.MAX_MISSED_INTERVIEWS
    can_book = missed < max_missed and app_data.get("status") == "active"
    rejection_imminent = missed == max_missed - 1

    rescheduled = app_data.get("reschedule_count", 0) or 0
    max_reschedules = scheduling.MAX_RESCHEDULES
    # In hard mode, exceeding the limit is blocked. In soft mode, the counter
    # is tracked but not enforced.
    if scheduling.RESCHEDULE_LIMIT_MODE == "hard":
        can_reschedule = rescheduled < max_reschedules
    else:
        can_reschedule = True

    return BookingState(
        booking_enabled=scheduling.BOOKING_ENABLED,
        application_id=application_id,
        booking=booking,
        missed_count=missed,
        max_missed=max_missed,
        reschedule_count=rescheduled,
        max_reschedules=max_reschedules,
        can_reschedule=can_reschedule,
        can_book=can_book,
        rejection_imminent=rejection_imminent,
    )


def _check_for_missed_booking(application_id: int) -> bool:
    """Check if there's a pending booking past the late grace and mark it missed.

    Returns True if a booking was just marked missed (caller may want to
    increment counters and notify the student). Idempotent — safe to call
    on every interview-related request.
    """
    booking_row = get_active_booking(application_id)
    if not booking_row:
        return False

    scheduled = scheduling.from_iso(booking_row["scheduled_at"])
    allowed, reason = scheduling.can_start_now(scheduled)
    if reason == "late":
        update_booking_status(booking_row["id"], "missed")
        new_count = increment_missed_interviews(application_id)

        # If we hit max missed, auto-reject the application
        if new_count >= scheduling.MAX_MISSED_INTERVIEWS:
            set_application_status(application_id, "rejected")
            app_data = get_application(application_id) or {}
            student_email = app_data.get("student_email", "")
            company_slug = app_data.get("company_slug", "")
            job_title = app_data.get("job_title", "")
            job = get_job(company_slug, app_data.get("job_slug", "")) or {}
            company_name = job.get("company", company_slug)
            student_name = (
                get_student_by_email(student_email)["name"]
                if student_email and get_student_by_email(student_email)
                else ""
            )
            notify(
                student_email=student_email,
                event="application_rejected",
                content=NotifyContent(
                    sender_name=f"{company_name} HR",
                    sender_role="Recruitment Team",
                    subject=f"Update on your application — {job_title}",
                    body=(
                        f"Dear {student_name or 'Candidate'},\n\n"
                        f"We were sorry to see that you missed your "
                        f"scheduled interview for the {job_title} role at "
                        f"{company_name}. After several missed appointments "
                        f"we are no longer able to progress your application.\n\n"
                        f"We wish you the best in your career and encourage "
                        f"you to apply for other roles where you can commit "
                        f"to the scheduled times.\n\n"
                        f"Best regards,\n"
                        f"{company_name} Recruitment\n\n"
                        f"\n{SIMULATION_NOTE_HEADER}\n\n"
                        f"You missed {new_count} scheduled interview(s) for "
                        f"this role and the application was automatically "
                        f"closed. In real recruitment, missing even one "
                        f"interview without notice usually ends the process. "
                        f"Treat your scheduled interview times as immovable "
                        f"commitments and arrive 5 minutes early."
                    ),
                    application_id=application_id,
                    related_stage="interview",
                ),
            )
        else:
            # Just notify they missed it and need to rebook
            app_data = get_application(application_id) or {}
            student_email = app_data.get("student_email", "")
            company_slug = app_data.get("company_slug", "")
            job_title = app_data.get("job_title", "")
            job = get_job(company_slug, app_data.get("job_slug", "")) or {}
            company_name = job.get("company", company_slug)
            student_name = (
                get_student_by_email(student_email)["name"]
                if student_email and get_student_by_email(student_email)
                else ""
            )
            remaining = scheduling.MAX_MISSED_INTERVIEWS - new_count
            notify(
                student_email=student_email,
                event="application_rejected",  # reuse — it's an inbox notification
                content=NotifyContent(
                    sender_name=f"{company_name} HR",
                    sender_role="Recruitment Team",
                    subject=f"Missed appointment — {job_title}",
                    body=(
                        f"Dear {student_name or 'Candidate'},\n\n"
                        f"We were expecting you for your interview for the "
                        f"{job_title} role at {company_name}, but you didn't "
                        f"join at the scheduled time. We understand things "
                        f"come up — please log into your WorkReady portal to "
                        f"reschedule.\n\n"
                        f"Please note that we can only offer a limited number "
                        f"of reschedules. After {scheduling.MAX_MISSED_INTERVIEWS} "
                        f"missed appointments your application will be closed.\n\n"
                        f"You currently have {remaining} reschedule(s) remaining.\n\n"
                        f"Best regards,\n"
                        f"{company_name} Recruitment"
                    ),
                    application_id=application_id,
                    related_stage="interview",
                ),
            )

        return True
    return False


@app.get("/api/v1/interview/{application_id}/booking", response_model=BookingState)
def get_booking(application_id: int) -> BookingState:
    """Get the current booking state for an application."""
    app_data = get_application(application_id)
    if not app_data:
        raise HTTPException(status_code=404, detail="Application not found")

    # Check if there's a stale booking that needs to be marked missed
    _check_for_missed_booking(application_id)

    # Re-fetch in case the missed check changed status
    app_data = get_application(application_id) or app_data
    return _build_booking_state(application_id, app_data)


@app.get("/api/v1/interview/{application_id}/slots", response_model=SlotOptions)
def get_booking_slots(
    application_id: int,
    days: str | None = None,
    time_of_day: str | None = None,
) -> SlotOptions:
    """Generate offered interview slots based on student preferences.

    Query params:
    - days: comma-separated ISO weekdays (1-7) the student is available
    - time_of_day: morning | afternoon | any
    """
    app_data = get_application(application_id)
    if not app_data:
        raise HTTPException(status_code=404, detail="Application not found")

    if not scheduling.BOOKING_ENABLED:
        raise HTTPException(
            status_code=400,
            detail="Interview booking is disabled in this environment",
        )

    company_slug = app_data.get("company_slug", "")
    cfg = _company_hours_config(company_slug)
    prefs = scheduling.SlotPreferences.from_query(days, time_of_day)
    # Default the preference days to the company's business days if the
    # student didn't specify (or specified days the company doesn't do)
    prefs.days = [d for d in prefs.days if d in cfg.days] or list(cfg.days)
    raw_slots = scheduling.generate_slots(prefs, config=cfg)

    slots = [
        SlotOption(
            scheduled_at=scheduling.to_iso(s),
            local_display=scheduling.to_local(s).strftime("%A %d %B, %I:%M %p"),
        )
        for s in raw_slots
    ]

    return SlotOptions(
        application_id=application_id,
        slots=slots,
        timezone=scheduling.TIMEZONE_NAME,
        business_hours=_format_business_hours_human(company_slug),
    )


@app.post("/api/v1/interview/{application_id}/book", response_model=BookingState)
def book_interview(application_id: int, req: BookingRequest) -> BookingState:
    """Book a specific interview slot."""
    app_data = get_application(application_id)
    if not app_data:
        raise HTTPException(status_code=404, detail="Application not found")

    if not scheduling.BOOKING_ENABLED:
        raise HTTPException(
            status_code=400,
            detail="Interview booking is disabled in this environment",
        )
    if app_data["current_stage"] != "interview":
        raise HTTPException(
            status_code=400,
            detail=f"Application is at stage '{app_data['current_stage']}', not 'interview'",
        )
    if app_data["status"] != "active":
        raise HTTPException(status_code=400, detail="Application is not active")

    missed = app_data.get("missed_interviews", 0) or 0
    if missed >= scheduling.MAX_MISSED_INTERVIEWS:
        raise HTTPException(
            status_code=400,
            detail="Too many missed interviews — this application is closed",
        )

    # Validate the requested time against the company's business hours
    try:
        scheduled = scheduling.from_iso(req.scheduled_at)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid time: {exc}") from exc

    company_slug = app_data.get("company_slug", "")
    cfg = _company_hours_config(company_slug)
    if not scheduling.is_business_time(scheduled, cfg):
        raise HTTPException(
            status_code=400,
            detail=f"Selected time is outside this company's business hours "
                   f"({cfg.human_summary()})",
        )
    if scheduled < scheduling.now_utc():
        raise HTTPException(status_code=400, detail="Selected time is in the past")

    # If there's an existing active booking, replacing it counts as a
    # reschedule (same as calling /cancel-booking first then /book).
    # This branch fires when the API is hit directly without going via
    # the portal's cancel-then-book flow.
    is_reschedule = False
    existing = get_active_booking(application_id)
    if existing:
        _try_use_reschedule(application_id, app_data)
        update_booking_status(existing["id"], "cancelled")
        delete_pending_messages_for_booking(existing["id"])
        is_reschedule = True
        # Re-fetch app_data after the counter changed
        app_data = get_application(application_id) or app_data
    elif (app_data.get("reschedule_count", 0) or 0) > 0:
        # No active booking right now, but the counter > 0 means a previous
        # cancel-booking call already counted this as a reschedule. We're
        # the new booking arriving after that cancel. Don't double count.
        is_reschedule = True

    # Create the new booking
    booking_id = create_booking(application_id, req.scheduled_at)

    # Send a confirmation message (immediate, never delayed).
    # For agency-mediated bookings the confirmation comes from the agency,
    # not the company. Confidential listings stay anonymous in the
    # confirmation — the company is only revealed when the interview
    # actually starts.
    student_email = app_data.get("student_email", "")
    job_title = app_data.get("job_title", "")
    company_slug = app_data.get("company_slug", "")
    job = get_job(company_slug, app_data.get("job_slug", "")) or {}
    company_name = job.get("company", company_slug)
    manager_name = job.get("reports_to", "the hiring manager")
    student_name = (
        get_student_by_email(student_email)["name"]
        if student_email and get_student_by_email(student_email)
        else ""
    )

    # Look up the posting to determine if this was via an agency
    posting = None
    posting_id = app_data.get("posting_id")
    if posting_id:
        posting = get_posting(posting_id)
    is_agency = bool(posting and posting.get("source_type") == "agency")
    is_confidential = bool(posting and posting.get("confidential"))
    agency_name = posting["agency_name"] if is_agency else None
    listing_title = posting["listing_title"] if posting else job_title

    # Decide who sends the confirmation and what the body says
    if is_agency:
        sender_name = agency_name
        sender_role = "Recruitment"
        signoff = agency_name
    else:
        sender_name = f"{company_name} HR"
        sender_role = "Recruitment Team"
        signoff = f"{company_name} Recruitment"

    # Subject and body adapt to confidentiality
    display_role = listing_title if is_confidential else job_title
    role_at_company = (
        f"the {listing_title} position"
        if is_confidential
        else f"the {job_title} role at {company_name}"
    )
    meeting_with_line = (
        "The interview will take place in your WorkReady portal — full details "
        "of the role and the team will be revealed when you join."
        if is_confidential
        else f"You'll be meeting with {manager_name}. The interview will "
        f"take place in your WorkReady portal."
    )

    local_time = scheduling.to_local(scheduled)
    nice_time = local_time.strftime("%A %d %B %Y at %I:%M %p %Z")

    # Calendar invite link — students can download and add to their calendar
    api_base = os.environ.get(
        "WORKREADY_API_PUBLIC_URL", "https://workready-api.eduserver.au"
    )
    ics_url = f"{api_base}/api/v1/interview/{application_id}/booking.ics"

    # Differentiate first booking from reschedule in the message subject/intro
    if is_reschedule:
        subject_prefix = "Updated confirmation"
        opening_line = (
            f"This is a confirmation of your **rescheduled** interview for "
            f"{role_at_company}. Your new interview time is:"
        )
        rescheduled_count = app_data.get("reschedule_count", 0) or 0
        max_r = scheduling.MAX_RESCHEDULES
        if (
            scheduling.RESCHEDULE_LIMIT_MODE == "hard"
            and rescheduled_count >= max_r
        ):
            reschedule_warning = (
                f"\n\nPlease note: this is your final reschedule. The booking "
                f"is now fixed and cannot be moved again. If you can't attend, "
                f"the application will be closed.\n"
            )
        else:
            remaining = max(max_r - rescheduled_count, 0)
            reschedule_warning = (
                f"\n\nPlease note: you have {remaining} reschedule(s) remaining "
                f"for this interview.\n"
            ) if scheduling.RESCHEDULE_LIMIT_MODE == "hard" else ""
    else:
        subject_prefix = "Interview confirmed"
        opening_line = f"Your interview for {role_at_company} is confirmed for:"
        reschedule_warning = ""

    notify(
        student_email=student_email,
        event="interview_invitation",  # closest event for now
        content=NotifyContent(
            sender_name=sender_name,
            sender_role=sender_role,
            subject=f"{subject_prefix} — {display_role} on {local_time.strftime('%a %d %b')}",
            body=(
                f"Dear {student_name or 'Candidate'},\n\n"
                f"{opening_line}\n\n"
                f"  {nice_time}\n\n"
                f"{meeting_with_line} Please log in a few minutes before "
                f"your scheduled time and click 'Begin Interview' from the "
                f"Interview view.\n\n"
                f"Add this appointment to your calendar:\n"
                f"  {ics_url}\n\n"
                f"Important: please arrive on time. We can only hold the "
                f"slot for {scheduling.LATE_GRACE_MINUTES} minutes after the "
                f"scheduled start."
                f"{reschedule_warning}\n\n"
                f"We look forward to meeting you.\n\n"
                f"Best regards,\n"
                f"{signoff}"
            ),
            application_id=application_id,
            booking_id=booking_id,
            related_stage="interview",
        ),
    )

    # Schedule reminder messages (24h and 1h before the interview).
    # These sit invisible in the inbox until their deliver_at arrives.
    _create_reminders(
        booking_id=booking_id,
        application_id=application_id,
        student_email=student_email,
        student_name=student_name,
        job_title=display_role,
        nice_time=nice_time,
        sender_name=sender_name,
        sender_role=sender_role,
        is_confidential=is_confidential,
        role_at_company=role_at_company,
        scheduled=scheduled,
    )

    return _build_booking_state(application_id, app_data)


def _build_ics(
    booking: dict,
    job_title: str,
    company_name: str,
    manager_name: str,
    sender_name: str,
    portal_url: str = "https://workready.eduserver.au",
) -> str:
    """Build an RFC 5545 .ics calendar invite for an interview booking.

    Returns a string with the iCalendar content. Uses a deterministic UID
    derived from the booking_id so calendar updates work if the booking
    is rescheduled (same UID, new DTSTART → calendar shows it as moved).
    """
    scheduled = scheduling.from_iso(booking["scheduled_at"])
    end = scheduled + timedelta(minutes=scheduling.SLOT_DURATION_MINUTES)
    now_stamp = scheduling.now_utc().strftime("%Y%m%dT%H%M%SZ")
    dtstart = scheduled.strftime("%Y%m%dT%H%M%SZ")
    dtend = end.strftime("%Y%m%dT%H%M%SZ")
    uid = f"workready-booking-{booking['id']}@workready.eduserver.au"

    summary = f"Interview: {job_title} at {company_name}"
    description = (
        f"Job interview for the {job_title} role at {company_name}. "
        f"You will be interviewed by {manager_name}. "
        f"The interview takes place in your WorkReady portal at {portal_url} — "
        f"please log in a few minutes early and click 'Begin Interview'. "
        f"You must arrive within {scheduling.LATE_GRACE_MINUTES} minutes of "
        f"the scheduled start or your slot will be forfeited."
    )
    # iCalendar requires CRLF line endings and 75-octet line folding.
    # For simplicity we keep our lines short enough not to need folding.

    def _escape(text: str) -> str:
        return (
            text.replace("\\", "\\\\")
            .replace(",", "\\,")
            .replace(";", "\\;")
            .replace("\n", "\\n")
        )

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//WorkReady//Interview Booking//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:REQUEST",
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{now_stamp}",
        f"DTSTART:{dtstart}",
        f"DTEND:{dtend}",
        f"SUMMARY:{_escape(summary)}",
        f"DESCRIPTION:{_escape(description)}",
        f"LOCATION:{_escape('WorkReady Portal — ' + portal_url)}",
        f"ORGANIZER;CN={_escape(sender_name)}:mailto:noreply@workready.eduserver.au",
        "STATUS:CONFIRMED",
        "TRANSP:OPAQUE",
        "BEGIN:VALARM",
        "ACTION:DISPLAY",
        f"DESCRIPTION:{_escape('Interview reminder — ' + summary)}",
        "TRIGGER:-PT15M",
        "END:VALARM",
        "BEGIN:VALARM",
        "ACTION:DISPLAY",
        f"DESCRIPTION:{_escape('Interview starts in 5 minutes')}",
        "TRIGGER:-PT5M",
        "END:VALARM",
        "END:VEVENT",
        "END:VCALENDAR",
    ]
    return "\r\n".join(lines) + "\r\n"


@app.get("/api/v1/interview/{application_id}/booking.ics")
def get_booking_ics(application_id: int) -> Response:
    """Return the .ics calendar invite for the current pending booking.

    Students download this and double-click to add the interview to their
    personal calendar (Google, Apple, Outlook all support .ics imports).
    """
    app_data = get_application(application_id)
    if not app_data:
        raise HTTPException(status_code=404, detail="Application not found")

    booking = get_active_booking(application_id)
    if not booking:
        raise HTTPException(status_code=404, detail="No active booking")

    company_slug = app_data.get("company_slug", "")
    job = get_job(company_slug, app_data.get("job_slug", "")) or {}
    company_name = job.get("company", company_slug)
    manager_name = job.get("reports_to", "the hiring manager")
    job_title = app_data.get("job_title", "")

    # Determine the sender (agency or company) based on the posting
    posting_id = app_data.get("posting_id")
    posting = get_posting(posting_id) if posting_id else None
    is_agency = bool(posting and posting.get("source_type") == "agency")
    is_confidential = bool(posting and posting.get("confidential"))

    if is_agency:
        sender_name = posting["agency_name"]
        # Confidential listings hide both company and manager in the calendar
        if is_confidential:
            company_name = "Confidential client"
            manager_name = "the hiring manager"
            job_title = posting["listing_title"]
    else:
        sender_name = f"{company_name} HR"

    ics = _build_ics(
        booking=booking,
        job_title=job_title,
        company_name=company_name,
        manager_name=manager_name,
        sender_name=sender_name,
    )

    filename = f"workready-interview-{booking['id']}.ics"
    return Response(
        content=ics,
        media_type="text/calendar; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


@app.post("/api/v1/interview/{application_id}/cancel-booking", response_model=BookingState)
def cancel_booking(application_id: int) -> BookingState:
    """Cancel the current pending booking (so the student can rebook).

    Counts as a reschedule. If the student is at the hard reschedule
    limit, this is rejected — they must attend the existing booking.

    Also deletes any pending reminder messages tied to this booking so
    they don't fire on the now-cancelled appointment.
    """
    app_data = get_application(application_id)
    if not app_data:
        raise HTTPException(status_code=404, detail="Application not found")

    booking = get_active_booking(application_id)
    if booking:
        # Cancelling counts as a reschedule. Check the limit before doing
        # anything destructive.
        _try_use_reschedule(application_id, app_data)
        update_booking_status(booking["id"], "cancelled")
        delete_pending_messages_for_booking(booking["id"])
        # Re-fetch app_data so the response shows the new counter
        app_data = get_application(application_id) or app_data

    return _build_booking_state(application_id, app_data)


# --- Stage 3: Interview ---


def _session_to_model(session: dict) -> InterviewSession:
    """Convert a DB session row + parsed transcript into an API model."""
    transcript = [InterviewMessage(**m) for m in (session.get("transcript") or [])]
    turn = sum(1 for m in transcript if m.role == "user")
    # Look up extra context for display (job title, company, manager role)
    app_data = get_application(session["application_id"]) or {}
    job = get_job(app_data.get("company_slug", ""), app_data.get("job_slug", "")) or {}
    return InterviewSession(
        session_id=session["id"],
        application_id=session["application_id"],
        manager_name=session["manager_name"],
        manager_role=_extract_manager_role(session.get("manager_name", ""), job),
        company_name=job.get("company", ""),
        job_title=app_data.get("job_title", ""),
        transcript=transcript,
        turn=turn,
        target_turns=TARGET_TURNS,
        status=session["status"],
        feedback=session.get("feedback"),
        final_score=session.get("final_score"),
    )


def _extract_manager_role(manager_name: str, job: dict) -> str:
    """Best-effort lookup of the manager's role from the job context."""
    persona = job.get("manager_persona", "")
    # Persona starts with "You are <Name>, <Role> at <Company>."
    if persona.startswith("You are "):
        try:
            after_name = persona.split(",", 1)[1].split(" at ")[0]
            return after_name.strip()
        except (IndexError, ValueError):
            return ""
    return ""


@app.post("/api/v1/interview/start", response_model=InterviewSession)
async def interview_start(req: InterviewStartRequest) -> InterviewSession:
    """Start an interview session for an application that's in the interview stage."""
    app_data = get_application(req.application_id)
    if not app_data:
        raise HTTPException(status_code=404, detail="Application not found")
    if app_data["current_stage"] != "interview":
        raise HTTPException(
            status_code=400,
            detail=f"Application is at stage '{app_data['current_stage']}', not 'interview'",
        )
    if app_data["status"] != "active":
        raise HTTPException(status_code=400, detail="Application is not active")

    # Booking enforcement: when enabled, the student must have a confirmed
    # booking and we must be within the grace window of the scheduled time.
    if scheduling.BOOKING_ENABLED:
        # First check if there's a stale booking that should be marked missed
        _check_for_missed_booking(req.application_id)
        # Re-fetch in case the missed check rejected the application
        app_data = get_application(req.application_id) or app_data
        if app_data["status"] != "active":
            raise HTTPException(
                status_code=400,
                detail="Application has been closed (too many missed appointments)",
            )

        booking = get_active_booking(req.application_id)
        if not booking:
            raise HTTPException(
                status_code=400,
                detail="No interview booked. Please schedule an appointment first.",
            )

        scheduled = scheduling.from_iso(booking["scheduled_at"])
        allowed, reason = scheduling.can_start_now(scheduled)
        if reason == "early":
            local_time = scheduling.to_local(scheduled).strftime(
                "%A %d %B at %I:%M %p"
            )
            raise HTTPException(
                status_code=400,
                detail=f"Your interview is scheduled for {local_time}. "
                f"Please come back then.",
            )
        if reason == "late":
            # _check_for_missed_booking already handled this, but defensive
            raise HTTPException(
                status_code=400,
                detail="You arrived too late. Please reschedule your interview.",
            )

        # Mark the booking as completed (we're starting the interview)
        update_booking_status(booking["id"], "completed")

    # Look up the job and resolve the manager (from interview pipeline)
    company_slug = app_data["company_slug"]
    job_slug = app_data["job_slug"]
    job = get_job(company_slug, job_slug)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    manager_persona = job.get("manager_persona", "")
    manager_name = job.get("reports_to", "Hiring Manager")
    company_name = job.get("company", company_slug)

    # Look up the most recent resume score for context
    resume_results = get_stage_results(req.application_id, "resume")
    resume_score = None
    resume_strengths: list[str] = []
    resume_gaps: list[str] = []
    if resume_results:
        latest = resume_results[-1]
        resume_score = latest.get("score")
        feedback = latest.get("feedback") or {}
        resume_strengths = feedback.get("strengths", [])
        resume_gaps = feedback.get("gaps", [])

    # Build the system prompt
    system_prompt = build_interview_system_prompt(
        manager_persona=manager_persona,
        job_title=app_data["job_title"],
        company_name=company_name,
        job_description=job.get("description", ""),
        resume_score=resume_score,
        resume_strengths=resume_strengths,
        resume_gaps=resume_gaps,
    )

    # Create the session
    session_id = create_interview_session(
        application_id=req.application_id,
        manager_slug=manager_name.lower().replace(" ", "-"),
        manager_name=manager_name,
    )

    # Get the opening message from the LLM
    opening = await chat_completion(system_prompt, [])
    append_interview_message(session_id, "assistant", opening)

    # Store the system prompt in the session for subsequent turns
    # (we re-build it on each turn for simplicity rather than storing it)

    session = get_interview_session(session_id)
    return _session_to_model(session)


@app.post("/api/v1/interview/message", response_model=InterviewMessageReply)
async def interview_message(req: InterviewMessageRequest) -> InterviewMessageReply:
    """Send a student message and get the manager's reply."""
    session = get_interview_session(req.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session["status"] != "active":
        raise HTTPException(status_code=400, detail="Session is not active")

    # Append the student's message to the transcript
    append_interview_message(req.session_id, "user", req.message.strip())

    # Re-build the system prompt with the current application context
    app_data = get_application(session["application_id"]) or {}
    job = get_job(app_data.get("company_slug", ""), app_data.get("job_slug", "")) or {}
    resume_results = get_stage_results(session["application_id"], "resume")
    resume_score = None
    resume_strengths: list[str] = []
    resume_gaps: list[str] = []
    if resume_results:
        latest = resume_results[-1]
        resume_score = latest.get("score")
        feedback = latest.get("feedback") or {}
        resume_strengths = feedback.get("strengths", [])
        resume_gaps = feedback.get("gaps", [])

    system_prompt = build_interview_system_prompt(
        manager_persona=job.get("manager_persona", ""),
        job_title=app_data.get("job_title", ""),
        company_name=job.get("company", ""),
        job_description=job.get("description", ""),
        resume_score=resume_score,
        resume_strengths=resume_strengths,
        resume_gaps=resume_gaps,
    )

    # Reload session to get updated transcript and send to LLM
    session = get_interview_session(req.session_id)
    transcript = session.get("transcript", [])
    reply = await chat_completion(system_prompt, transcript)
    append_interview_message(req.session_id, "assistant", reply)

    # Calculate turn count
    final_session = get_interview_session(req.session_id)
    turn = sum(1 for m in final_session.get("transcript", []) if m["role"] == "user")

    return InterviewMessageReply(
        session_id=req.session_id,
        reply=reply,
        turn=turn,
        target_turns=TARGET_TURNS,
        suggested_wrap_up=turn >= WRAP_UP_AFTER,
    )


@app.post("/api/v1/interview/{session_id}/end", response_model=InterviewSession)
async def interview_end(session_id: int) -> InterviewSession:
    """End the interview, run the assessment, and update the application."""
    session = get_interview_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session["status"] != "active":
        raise HTTPException(status_code=400, detail="Session is not active")

    application_id = session["application_id"]
    app_data = get_application(application_id) or {}
    job = get_job(app_data.get("company_slug", ""), app_data.get("job_slug", "")) or {}
    job_title = app_data.get("job_title", "")
    company_name = job.get("company", "")

    # Run the LLM assessment on the transcript
    transcript = session.get("transcript", [])
    result = await assess_interview(
        job_title=job_title,
        company_name=company_name,
        transcript=transcript,
    )

    # Persist the assessment
    feedback_dict = result.feedback.model_dump()
    complete_interview_session(
        session_id=session_id,
        final_score=result.fit_score,
        feedback={
            "fit_score": result.fit_score,
            "feedback": feedback_dict,
            "proceed": result.proceed_to_interview,
            "summary": result.message,
        },
    )

    # Record stage_result and advance/reject the application
    record_stage_result(
        application_id=application_id,
        stage="interview",
        status="passed" if result.proceed_to_interview else "failed",
        score=result.fit_score,
        feedback=feedback_dict,
    )

    # Notify the student via personal inbox
    student_email = app_data.get("student_email", "")
    student_name = (
        get_student_by_email(student_email)["name"]
        if student_email and get_student_by_email(student_email)
        else ""
    )
    feedback_block = _format_interview_feedback(feedback_dict, result.fit_score)

    # Interview feedback + placement onboarding share one deliver_at so the
    # HR note, the mentor's welcome, and the first task brief all land
    # together after the configured delay (+ jitter). Pattern mirrors the
    # resume flow's RESUME_FEEDBACK_DELAY_MINUTES handling above.
    interview_deliver_at = scheduling.feedback_delivery_time(
        scheduling.INTERVIEW_FEEDBACK_DELAY_MINUTES,
        scheduling.INTERVIEW_FEEDBACK_DELAY_JITTER_MINUTES,
    )

    if result.proceed_to_interview:
        # Passed — advance to work_task stage and activate placement:
        # creates 3 gated tasks, flips status to hired, schedules the
        # mentor's welcome + first task brief to deliver_at.
        advance_stage(application_id, "work_task")
        activate_work_placement(application_id, interview_deliver_at)
        notify(
            student_email=student_email,
            event="interview_passed",
            content=NotifyContent(
                sender_name=f"{company_name} HR",
                sender_role="Recruitment Team",
                subject=f"Great news — you're moving forward at {company_name}",
                body=(
                    f"Dear {student_name or 'Candidate'},\n\n"
                    f"Thank you for taking the time to interview for the "
                    f"{job_title} role at {company_name}. We enjoyed our "
                    f"conversation and would like to welcome you to the team.\n\n"
                    f"Your mentor will be in touch shortly with your first "
                    f"brief — look out for it in your work inbox.\n\n"
                    f"Best regards,\n"
                    f"{company_name} Recruitment\n\n"
                    f"\n{feedback_block}"
                ),
                application_id=application_id,
                related_stage="work_task",
                deliver_at=interview_deliver_at,
            ),
        )
    else:
        # Failed interview — reject the application (company goes off-board)
        set_application_status(application_id, "rejected")
        notify(
            student_email=student_email,
            event="application_rejected",
            content=NotifyContent(
                sender_name=f"{company_name} HR",
                sender_role="Recruitment Team",
                subject=f"Update on your interview — {job_title}",
                body=(
                    f"Dear {student_name or 'Candidate'},\n\n"
                    f"Thank you for taking the time to interview for the "
                    f"{job_title} role at {company_name}. After careful "
                    f"consideration, we have decided not to progress your "
                    f"application at this time.\n\n"
                    f"We had a strong field of candidates and the decision "
                    f"was a difficult one. We wish you the best in your "
                    f"career and encourage you to apply for other roles.\n\n"
                    f"Best regards,\n"
                    f"{company_name} Recruitment\n\n"
                    f"\n{feedback_block}"
                ),
                application_id=application_id,
                related_stage="interview",
                deliver_at=interview_deliver_at,
            ),
        )

    final = get_interview_session(session_id)
    return _session_to_model(final)


@app.get("/api/v1/interview/{session_id}", response_model=InterviewSession)
def get_interview(session_id: int) -> InterviewSession:
    """Get an interview session (active or completed) including the transcript."""
    session = get_interview_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return _session_to_model(session)


# --- Stage 4: Work tasks --------------------------------------------------


def _effective_task_status(task: dict) -> str:
    """Return the status a task should report to the student.

    A submission's outcome is lazy-gated by review_deliver_at — until the
    delay has elapsed, the student sees 'under_review' even though the
    reviewer's JSON is already stored on the submission row.
    """
    status = task.get("status", "assigned")
    if status == "submitted":
        sub = get_latest_submission(task["id"])
        if sub and sub.get("review_deliver_at"):
            now = scheduling.to_iso(scheduling.now_utc())
            if sub["review_deliver_at"] <= now and sub.get("review_status"):
                # Outcome is ready to reveal — flip the task row and return it
                mark_task_reviewed(task["id"], sub["review_status"])
                return sub["review_status"]
        return "under_review"
    return status


def _task_to_summary(task: dict) -> TaskSummary:
    return TaskSummary(
        id=task["id"],
        sequence=task["sequence"],
        title=task["title"],
        brief=task["brief"],
        difficulty=task["difficulty"],
        status=_effective_task_status(task),
        visible_at=task.get("visible_at"),
        due_at=task.get("due_at"),
        submitted_at=task.get("submitted_at"),
        reviewed_at=task.get("reviewed_at"),
    )


@app.get("/api/v1/tasks/application/{application_id}", response_model=TaskList)
def list_tasks(application_id: int) -> TaskList:
    """List all visible tasks for an application (hides gated tasks)."""
    app_data = get_application(application_id)
    if not app_data:
        raise HTTPException(status_code=404, detail="Application not found")
    tasks = list_tasks_for_application(application_id, only_visible=True)
    return TaskList(
        application_id=application_id,
        total=len(tasks),
        tasks=[_task_to_summary(t) for t in tasks],
    )


def _task_to_detail(task: dict) -> TaskDetail:
    status = _effective_task_status(task)
    detail = TaskDetail(
        id=task["id"],
        sequence=task["sequence"],
        title=task["title"],
        brief=task["brief"],
        description=task["description"],
        difficulty=task["difficulty"],
        status=status,
        visible_at=task.get("visible_at"),
        due_at=task.get("due_at"),
        submitted_at=task.get("submitted_at"),
        reviewed_at=task.get("reviewed_at"),
    )
    sub = get_latest_submission(task["id"])
    if sub:
        detail.submission_body = sub.get("body")
        detail.attachment_filename = sub.get("attachment_filename")
        # Only reveal score/feedback if the delay has elapsed
        now = scheduling.to_iso(scheduling.now_utc())
        if (sub.get("review_deliver_at") or "") <= now:
            detail.score = sub.get("score")
            fb = sub.get("feedback")
            if fb:
                detail.feedback = TaskFeedback(
                    strengths=fb.get("strengths", []),
                    improvements=fb.get("improvements", []),
                    summary=fb.get("summary", ""),
                )
    return detail


@app.get("/api/v1/tasks/{task_id}", response_model=TaskDetail)
def get_task_detail(task_id: int) -> TaskDetail:
    """Get full detail for a single task (description + latest submission)."""
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    # Gated tasks are 404 to the student — they shouldn't know they exist
    if not task.get("visible_at"):
        raise HTTPException(status_code=404, detail="Task not found")
    now = scheduling.to_iso(scheduling.now_utc())
    if task["visible_at"] > now:
        raise HTTPException(status_code=404, detail="Task not found")
    return _task_to_detail(task)


@app.post("/api/v1/tasks/{task_id}/submit", response_model=TaskSubmitResult)
async def submit_task(
    task_id: int,
    body: str = Form(...),
    attachment: UploadFile | None = File(None),
) -> TaskSubmitResult:
    """Submit a work task. Runs the mentor reviewer, stores the outcome
    lazily-gated behind TASK_FEEDBACK_DELAY, and reveals the next task
    after TASK_NEXT_TASK_DELAY.
    """
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    now_iso = scheduling.to_iso(scheduling.now_utc())
    if not task.get("visible_at") or task["visible_at"] > now_iso:
        raise HTTPException(status_code=404, detail="Task not found")
    if task["status"] not in ("assigned", "resubmit"):
        raise HTTPException(
            status_code=400,
            detail=f"Task cannot be submitted in status '{task['status']}'",
        )

    # Optional PDF attachment → extract text for LLM context
    attachment_text: str | None = None
    attachment_filename: str | None = None
    if attachment is not None:
        pdf_bytes = await attachment.read()
        if pdf_bytes:
            attachment_filename = attachment.filename
            try:
                attachment_text = extract_text(pdf_bytes)
            except Exception:  # noqa: BLE001
                attachment_text = None

    application_id = task["application_id"]
    app_data = get_application(application_id) or {}
    job = get_job(app_data.get("company_slug", ""), app_data.get("job_slug", "")) or {}
    mentor_persona = job.get("manager_persona", "") or ""
    company_name = job.get("company", "")
    mentor_name = job.get("reports_to", "Your mentor")

    # Build prior task history for the reviewer
    history = list_prior_task_history(application_id, before_sequence=task["sequence"])

    # Late flag
    late_by_days = 0
    due_at = task.get("due_at")
    if due_at and due_at < now_iso:
        try:
            delta = scheduling.now_utc() - scheduling.from_iso(due_at)
            late_by_days = max(0, delta.days)
        except Exception:  # noqa: BLE001
            late_by_days = 0

    # Run the mentor reviewer (sync LLM call — the outcome is gated on read)
    score, outcome, feedback = await review_task_submission(
        manager_persona=mentor_persona,
        company_name=company_name,
        task_title=task["title"],
        task_brief=task["brief"],
        task_description=task["description"],
        difficulty=task["difficulty"],
        submission_body=body,
        attachment_text=attachment_text,
        prior_history=history,
        late_by_days=late_by_days,
    )

    # Compute when the feedback is allowed to reveal
    review_deliver_at = scheduling.feedback_delivery_time(
        scheduling.TASK_FEEDBACK_DELAY_MINUTES,
        scheduling.TASK_FEEDBACK_DELAY_JITTER_MINUTES,
    )

    # Persist the submission with its stored (but gated) outcome
    create_task_submission(
        task_id=task_id,
        body=body,
        score=score,
        feedback=feedback.model_dump(),
        review_status=outcome,
        review_deliver_at=review_deliver_at,
        attachment_filename=attachment_filename,
        attachment_text=attachment_text,
    )
    mark_task_submitted(task_id)
    # Mark the task's deadline calendar event as completed so the
    # calendar view doesn't keep showing it as upcoming.
    cancel_task_deadline_event(task_id)

    # Reveal the next task (with its own small delay) — this normally
    # lands BEFORE the feedback email, so the student starts the new
    # task before the mentor's notes on the prior one arrive.
    next_revealed = reveal_next_task_after_submission(application_id)
    is_final_task = next_revealed is None

    # Schedule the mentor's feedback email (lazy-delivered via deliver_at)
    student = get_student_by_email(app_data.get("student_email", ""))
    if student:
        bullet = lambda items: "\n".join(f"  • {s}" for s in items) if items else "  • (none)"
        if is_final_task:
            closing_line = (
                "That's your last task for this internship — good work "
                "getting through them. I'll be in touch about the wrap-up "
                "conversation shortly."
            )
        else:
            closing_line = (
                "Take these notes on board and carry them into your next "
                "brief — that's how you'll get better, one task at a time."
            )
        feedback_body = (
            f"Hi {student['name'].split()[0] if student['name'] else 'there'},\n\n"
            f"I've had a look at your submission for \"{task['title']}\".\n\n"
            f"{feedback.summary}\n\n"
            f"WHAT WORKED:\n{bullet(feedback.strengths)}\n\n"
            f"WHAT TO STRENGTHEN NEXT TIME:\n{bullet(feedback.improvements)}\n\n"
            f"Outcome: {outcome.upper()}  •  Score: {score}/100\n\n"
            f"{closing_line}\n\n"
            f"— {mentor_name}"
        )
        from workready_api.db import create_message
        create_message(
            student_id=student["id"],
            student_email=student["email"],
            sender_name=mentor_name,
            sender_role=f"Your mentor at {company_name}",
            subject=f"Feedback on your task — {task['title']}",
            body=feedback_body,
            inbox="work",
            application_id=application_id,
            related_stage="work_task",
            deliver_at=review_deliver_at,
        )

        # Final-task handoff: the student has finished all work tasks.
        # Advance the application stage so Stage 6 (exit interview) can
        # pick it up when it's built. We skip over 'lunchroom' for now —
        # Stage 5 will wire that in later. Also emit an in-app "work
        # phase complete" message so the student has a clear signal.
        #
        # TODO stage 6: trigger_exit_interview(application_id) once built.
        if is_final_task:
            advance_stage(application_id, "exit_interview")
            wrapup_body = (
                f"Hi {student['name'].split()[0] if student['name'] else 'there'},\n\n"
                f"You've completed all of your work tasks at {company_name}. "
                f"Before we wrap up your internship, we'll schedule a short "
                f"exit conversation to reflect on what you learned and what "
                f"you'd do differently. Someone will be in touch with the "
                f"details shortly.\n\n"
                f"Congratulations on getting through the program — take a "
                f"moment to recognise that.\n\n"
                f"— {company_name}"
            )
            create_message(
                student_id=student["id"],
                student_email=student["email"],
                sender_name=f"{company_name}",
                sender_role="HR",
                subject=f"Work phase complete — {company_name}",
                body=wrapup_body,
                inbox="work",
                application_id=application_id,
                related_stage="exit_interview",
                deliver_at=review_deliver_at,
            )

    # Stage 5a: lunchroom invitation hook. When LUNCHROOM_TRIGGER is
    # 'task_review' (default), each task submission may create a new
    # lunchroom invitation — up to LUNCHROOM_INVITES total per
    # application. The invitation appears as a work-inbox message
    # shortly after the feedback email, offering 3 lunchtime slots.
    # create_invitation is idempotent-ish (respects the cap itself)
    # and returns None if the cap is hit or no participants/slots
    # can be picked.
    if scheduling.LUNCHROOM_TRIGGER == "task_review":
        try:
            lunchroom_mod.create_invitation(
                application=app_data,
                trigger_source="task_review",
                trigger_task=task,
            )
        except Exception:  # noqa: BLE001
            # A lunchroom failure shouldn't break the submit flow —
            # the student's task submission and feedback are the
            # critical path. Log and continue.
            import logging
            logging.getLogger(__name__).exception(
                "Lunchroom invitation creation failed for application %d "
                "on task %d", application_id, task_id,
            )

    # The response is deliberately lazy-gated too: until the feedback
    # delay elapses, we report 'under_review' and no score/feedback.
    review_ready = review_deliver_at <= scheduling.to_iso(scheduling.now_utc())
    if review_ready:
        return TaskSubmitResult(
            task_id=task_id,
            status=outcome,
            score=score,
            feedback=feedback,
            message="Review ready.",
        )
    return TaskSubmitResult(
        task_id=task_id,
        status="under_review",
        score=None,
        feedback=None,
        message="Your submission has been received. Your mentor will "
                "review it and get back to you shortly.",
    )


# --- Stage 4c: Calendar ---------------------------------------------------


def _event_to_model(row: dict) -> CalendarEvent:
    return CalendarEvent(
        id=row["id"],
        event_type=row["event_type"],
        title=row["title"],
        description=row.get("description"),
        scheduled_at=row["scheduled_at"],
        status=row["status"],
        related_id=row.get("related_id"),
        created_at=row["created_at"],
    )


@app.get(
    "/api/v1/calendar/application/{application_id}",
    response_model=CalendarEventList,
)
def list_calendar(
    application_id: int,
    include_past: bool = True,
) -> CalendarEventList:
    """List calendar events for an application, chronologically.

    include_past=False (query param) hides events whose scheduled_at is
    in the past — useful for the portal's "upcoming" view.
    """
    app_data = get_application(application_id)
    if not app_data:
        raise HTTPException(status_code=404, detail="Application not found")
    rows = list_calendar_events(
        application_id,
        include_past=include_past,
        include_cancelled=False,
    )
    return CalendarEventList(
        application_id=application_id,
        events=[_event_to_model(r) for r in rows],
        total=len(rows),
    )


@app.post(
    "/api/v1/calendar/event/{event_id}/accept",
    response_model=CalendarEvent,
)
def accept_calendar_event(event_id: int) -> CalendarEvent:
    """Accept an invitation-style calendar event (e.g. lunchroom)."""
    event = get_calendar_event(event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    if event["status"] not in ("upcoming",):
        raise HTTPException(
            status_code=400,
            detail=f"Event cannot be accepted in status '{event['status']}'",
        )
    update_calendar_event_status(event_id, "accepted")
    return _event_to_model(get_calendar_event(event_id) or event)


@app.post(
    "/api/v1/calendar/event/{event_id}/decline",
    response_model=CalendarEvent,
)
def decline_calendar_event(event_id: int) -> CalendarEvent:
    """Decline an invitation-style calendar event."""
    event = get_calendar_event(event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    if event["status"] not in ("upcoming",):
        raise HTTPException(
            status_code=400,
            detail=f"Event cannot be declined in status '{event['status']}'",
        )
    update_calendar_event_status(event_id, "declined")
    return _event_to_model(get_calendar_event(event_id) or event)


# --- Stage 5: Lunchroom ---------------------------------------------------


def _lunchroom_session_to_model(row: dict) -> LunchroomSession:
    """Convert a decoded lunchroom_sessions dict to a Pydantic model."""
    participants = [
        LunchroomParticipant(
            slug=p.get("slug", ""),
            name=p.get("name", ""),
            role=p.get("role", "") or "",
        )
        for p in (row.get("participants") or [])
    ]
    slots = [
        LunchroomSlot(
            scheduled_at=s,
            local_display=lunchroom_mod._format_slot_human(s),
        )
        for s in (row.get("proposed_slots") or [])
    ]
    return LunchroomSession(
        id=row["id"],
        application_id=row["application_id"],
        occasion=row["occasion"],
        occasion_detail=row.get("occasion_detail"),
        participants=participants,
        proposed_slots=slots,
        scheduled_at=row.get("scheduled_at"),
        status=row["status"],
        trigger_source=row.get("trigger_source"),
        invitation_message_id=row.get("invitation_message_id"),
        calendar_event_id=row.get("calendar_event_id"),
        created_at=row["created_at"],
        completed_at=row.get("completed_at"),
    )


@app.get(
    "/api/v1/lunchroom/application/{application_id}",
    response_model=LunchroomSessionList,
)
def list_lunchroom_sessions(application_id: int) -> LunchroomSessionList:
    """List all lunchroom sessions (invites + accepted + completed) for an app."""
    app_data = get_application(application_id)
    if not app_data:
        raise HTTPException(status_code=404, detail="Application not found")
    rows = list_lunchroom_sessions_for_application(application_id)
    return LunchroomSessionList(
        application_id=application_id,
        sessions=[_lunchroom_session_to_model(r) for r in rows],
        total=len(rows),
    )


@app.get(
    "/api/v1/lunchroom/session/{session_id}",
    response_model=LunchroomSession,
)
def get_lunchroom(session_id: int) -> LunchroomSession:
    """Get a single lunchroom session by ID."""
    session = get_lunchroom_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return _lunchroom_session_to_model(session)


@app.post(
    "/api/v1/lunchroom/invitation/{session_id}/pick-slot",
    response_model=LunchroomSession,
)
def pick_lunchroom_slot_route(
    session_id: int, req: LunchroomSlotPickRequest,
) -> LunchroomSession:
    """Student picks one of the proposed slots.

    Validates the picked ISO matches one of the proposed slots, transitions
    the session to 'accepted', and materialises a calendar event so it
    shows up in the calendar view.
    """
    session = get_lunchroom_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session["status"] != "invited":
        raise HTTPException(
            status_code=400,
            detail=f"Session cannot be picked in status '{session['status']}'",
        )
    if req.scheduled_at not in (session.get("proposed_slots") or []):
        raise HTTPException(
            status_code=400,
            detail="Picked slot is not one of the proposed options",
        )
    updated = lunchroom_mod.accept_slot(session_id, req.scheduled_at)
    if not updated:
        raise HTTPException(status_code=500, detail="Failed to accept slot")
    return _lunchroom_session_to_model(updated)


@app.post(
    "/api/v1/lunchroom/invitation/{session_id}/decline",
    response_model=LunchroomSession,
)
def decline_lunchroom_invitation_route(session_id: int) -> LunchroomSession:
    """Student declines the invitation. May trigger a mentor check-in."""
    session = get_lunchroom_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session["status"] != "invited":
        raise HTTPException(
            status_code=400,
            detail=f"Session cannot be declined in status '{session['status']}'",
        )
    updated = lunchroom_mod.decline(session_id)
    if not updated:
        raise HTTPException(status_code=500, detail="Failed to decline invitation")
    # Possibly trigger the gentle mentor check-in if the decline threshold
    # has been reached (fires at most once per application).
    lunchroom_mod.maybe_send_decline_check_in(session["application_id"])
    return _lunchroom_session_to_model(updated)

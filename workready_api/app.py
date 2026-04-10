"""FastAPI application for the WorkReady Simulation API."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from workready_api.assessor import assess
from workready_api.blocking import get_blocked_for_student
from workready_api.db import (
    advance_stage,
    create_application,
    get_application,
    get_db,
    get_inbox,
    get_or_create_student,
    get_posting,
    get_stage_results,
    get_student_applications,
    get_student_by_email,
    init_db,
    mark_message_read,
    record_stage_result,
    set_application_status,
)
from workready_api.jobs import (
    get_job,
    get_job_description,
    load_jobs,
    seed_postings_from_jobs,
)
from workready_api.notifications import NotifyContent, notify
from workready_api.models import (
    ApplicationDetail,
    ApplicationSummary,
    AssessmentResult,
    BlockedJob,
    Inbox,
    Message,
    StageResult,
    StudentProgress,
    StudentState,
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


# --- Helpers ---


def _format_bullets(items: list[str], indent: str = "  • ") -> str:
    """Format a list as a bullet list, returning '(none)' for empty."""
    if not items:
        return f"{indent}(none)"
    return "\n".join(f"{indent}{item}" for item in items)


def _format_resume_feedback(feedback: dict, fit_score: int) -> str:
    """Format the resume assessment feedback as a plain-text block."""
    return (
        f"YOUR APPLICATION SUMMARY\n"
        f"────────────────────────\n"
        f"Overall fit score: {fit_score}/100\n\n"
        f"WHAT WORKED WELL\n"
        f"{_format_bullets(feedback.get('strengths', []))}\n\n"
        f"AREAS FOR IMPROVEMENT\n"
        f"{_format_bullets(feedback.get('gaps', []))}\n\n"
        f"SUGGESTIONS\n"
        f"{_format_bullets(feedback.get('suggestions', []))}\n\n"
        f"TAILORING ASSESSMENT\n"
        f"  {feedback.get('tailoring', '(none)')}"
    )


# --- Health ---


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "version": "0.2.0"}


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
                    f"Below is a summary of how your application was assessed, "
                    f"so you can prepare with confidence.\n\n"
                    f"{feedback_block}\n\n"
                    f"We look forward to meeting you.\n\n"
                    f"Best regards,\n"
                    f"{company_name} Recruitment"
                ),
                application_id=application_id,
                related_stage="interview",
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
                    f"progress your application at this time. We've included "
                    f"detailed feedback below — review it carefully, then apply "
                    f"for another role that might be a stronger fit.\n\n"
                    f"{feedback_block}\n\n"
                    f"We wish you the best in your career.\n\n"
                    f"Best regards,\n"
                    f"{reject_signoff}"
                ),
                application_id=application_id,
                related_stage="resume",
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
            **{k: v for k, v in app_data.items() if k != "student_email"}
        ),
        stages=[
            StageResult(**s)
            for s in stages
        ],
    )

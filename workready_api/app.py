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
    append_interview_message,
    complete_interview_session,
    create_application,
    create_interview_session,
    get_all_postings,
    get_application,
    get_db,
    get_inbox,
    get_interview_session,
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
from workready_api.interview import (
    TARGET_TURNS,
    WRAP_UP_AFTER,
    assess_interview,
    build_interview_system_prompt,
    chat_completion,
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
    InterviewMessage,
    InterviewMessageReply,
    InterviewMessageRequest,
    InterviewSession,
    InterviewStartRequest,
    Message,
    PostingList,
    PublicPosting,
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
            **{k: v for k, v in app_data.items() if k not in ("student_email", "student_id")}
        ),
        stages=[
            StageResult(**s)
            for s in stages
        ],
    )


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
    feedback_block = (
        f"INTERVIEW SUMMARY\n"
        f"────────────────────────\n"
        f"Overall score: {result.fit_score}/100\n\n"
        f"WHAT WORKED WELL\n"
        f"{_format_bullets(feedback_dict.get('strengths', []))}\n\n"
        f"AREAS FOR IMPROVEMENT\n"
        f"{_format_bullets(feedback_dict.get('gaps', []))}\n\n"
        f"SUGGESTIONS\n"
        f"{_format_bullets(feedback_dict.get('suggestions', []))}\n\n"
        f"OVERALL\n"
        f"  {result.message or feedback_dict.get('tailoring', '')}"
    )

    if result.proceed_to_interview:
        # Passed — advance to work_task stage. Stage 4 isn't built yet,
        # but the state will sit there until it is.
        advance_stage(application_id, "work_task")
        notify(
            student_email=student_email,
            event="interview_invitation",  # reusing the event type for now
            content=NotifyContent(
                sender_name=f"{company_name} HR",
                sender_role="Recruitment Team",
                subject=f"Great news — you're moving forward at {company_name}",
                body=(
                    f"Dear {student_name or 'Candidate'},\n\n"
                    f"Thank you for taking the time to interview for the "
                    f"{job_title} role at {company_name}. We were impressed "
                    f"by what you brought to the conversation and would like "
                    f"to invite you to the next stage.\n\n"
                    f"You'll find the next steps in your WorkReady portal.\n\n"
                    f"Below is a summary of how the interview went so you can "
                    f"reflect on your performance.\n\n"
                    f"{feedback_block}\n\n"
                    f"Best regards,\n"
                    f"{company_name} Recruitment"
                ),
                application_id=application_id,
                related_stage="work_task",
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
                    f"We've included detailed feedback below — review it "
                    f"carefully and apply for another role that might be a "
                    f"stronger fit.\n\n"
                    f"{feedback_block}\n\n"
                    f"We wish you the best in your career.\n\n"
                    f"Best regards,\n"
                    f"{company_name} Recruitment"
                ),
                application_id=application_id,
                related_stage="interview",
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

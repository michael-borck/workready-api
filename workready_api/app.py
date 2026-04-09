"""FastAPI application for the WorkReady Simulation API."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from workready_api.assessor import assess
from workready_api.db import (
    advance_stage,
    create_application,
    create_message,
    get_application,
    get_db,
    get_inbox,
    get_or_create_student,
    get_stage_results,
    get_student_applications,
    init_db,
    mark_message_read,
    record_stage_result,
)
from workready_api.jobs import get_job, get_job_description, load_jobs
from workready_api.models import (
    ApplicationDetail,
    ApplicationSummary,
    AssessmentResult,
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
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Health ---


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "version": "0.2.0"}


# --- Stage 2: Resume submission ---


@app.post("/api/v1/resume", response_model=AssessmentResult)
async def submit_resume(
    company_slug: str = Form(...),
    job_slug: str = Form(...),
    job_title: str = Form(...),
    applicant_name: str = Form(...),
    applicant_email: str = Form(...),
    cover_letter: str = Form(""),
    source: str = Form("direct"),
    resume: UploadFile = File(...),
) -> AssessmentResult:
    """Stage 2 — Submit a resume for assessment.

    Creates a student record (if new), creates an application,
    assesses the resume, and records the result.
    """
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
    get_or_create_student(applicant_email, applicant_name)

    application_id = create_application(
        student_email=applicant_email,
        company_slug=company_slug,
        job_slug=job_slug,
        job_title=job_title,
        source=source,
    )

    record_stage_result(
        application_id=application_id,
        stage="resume",
        status="passed" if result.proceed_to_interview else "failed",
        score=result.fit_score,
        feedback=result.feedback.model_dump(),
    )

    # Personal inbox: confirmation message (immediate)
    create_message(
        student_email=applicant_email,
        sender_name="WorkReady Jobs",
        sender_role="Application System",
        subject=f"Application received — {job_title}",
        body=(
            f"Hi {applicant_name},\n\n"
            f"Thank you for applying for the {job_title} position. "
            f"We have received your application and it is now under review. "
            f"You will hear back from us shortly.\n\n"
            f"— WorkReady Jobs"
        ),
        inbox="personal",
        application_id=application_id,
        related_stage="resume",
    )

    # Personal inbox: outcome message
    job_meta = get_job(company_slug, job_slug)
    company_name = job_meta["company"] if job_meta else company_slug
    if result.proceed_to_interview:
        advance_stage(application_id, "interview")
        create_message(
            student_email=applicant_email,
            sender_name=f"{company_name} HR",
            sender_role="Recruitment Team",
            subject=f"Interview invitation — {job_title}",
            body=(
                f"Dear {applicant_name},\n\n"
                f"Thank you for your application for the {job_title} role at "
                f"{company_name}. We were impressed by your application and "
                f"would like to invite you to an interview.\n\n"
                f"Please log into your WorkReady portal to schedule your "
                f"interview at your earliest convenience.\n\n"
                f"We look forward to meeting you.\n\n"
                f"Best regards,\n"
                f"{company_name} Recruitment"
            ),
            inbox="personal",
            application_id=application_id,
            related_stage="interview",
        )
    else:
        create_message(
            student_email=applicant_email,
            sender_name=f"{company_name} HR",
            sender_role="Recruitment Team",
            subject=f"Update on your application — {job_title}",
            body=(
                f"Dear {applicant_name},\n\n"
                f"Thank you for your interest in the {job_title} role at "
                f"{company_name} and for taking the time to submit your "
                f"application.\n\n"
                f"After careful consideration, we have decided not to "
                f"progress your application at this time. We encourage you "
                f"to review the feedback in your WorkReady portal and apply "
                f"for other roles that may be a stronger fit.\n\n"
                f"We wish you the best in your career.\n\n"
                f"Best regards,\n"
                f"{company_name} Recruitment"
            ),
            inbox="personal",
            application_id=application_id,
            related_stage="resume",
        )

    result.application_id = application_id
    return result


# --- Student progress ---


@app.get("/api/v1/student/{email}", response_model=StudentProgress)
def get_student_progress(email: str) -> StudentProgress:
    """Get all applications and progress for a student."""
    applications = get_student_applications(email)
    if not applications:
        raise HTTPException(status_code=404, detail="Student not found")

    # Get student name
    with get_db() as conn:
        student = conn.execute(
            "SELECT name FROM students WHERE email = ?", (email,)
        ).fetchone()
    name = student["name"] if student else email

    return StudentProgress(
        email=email,
        name=name,
        applications=[
            ApplicationSummary(**{k: v for k, v in a.items() if k != "student_email"})
            for a in applications
        ],
    )


@app.get("/api/v1/student/{email}/state", response_model=StudentState)
def get_student_state(email: str) -> StudentState:
    """Get the high-level state of a student for the portal.

    Returns the state machine value (NOT_APPLIED, APPLIED, HIRED, COMPLETED),
    active application if any, and unread message counts.
    """
    with get_db() as conn:
        student = conn.execute(
            "SELECT * FROM students WHERE email = ?", (email,)
        ).fetchone()

    if not student:
        # Auto-create student on first lookup with no applications
        return StudentState(
            email=email,
            name=email.split("@")[0],
            state="NOT_APPLIED",
            active_application=None,
            applications=[],
            unread_personal=0,
            unread_work=0,
        )

    applications = get_student_applications(email)

    # Determine state from most recent application
    state = "NOT_APPLIED"
    active = None
    if applications:
        latest = applications[0]
        active = ApplicationSummary(
            **{k: v for k, v in latest.items() if k != "student_email"}
        )
        stage = latest["current_stage"]
        if stage == "resume":
            state = "APPLIED"
        elif stage in ("interview", "work_task", "lunchroom", "exit_interview"):
            state = "HIRED"
        elif stage == "completed":
            state = "COMPLETED"

    # Count unread messages per inbox
    personal_msgs = get_inbox(email, "personal")
    work_msgs = get_inbox(email, "work")
    unread_personal = sum(1 for m in personal_msgs if not m.get("is_read"))
    unread_work = sum(1 for m in work_msgs if not m.get("is_read"))

    return StudentState(
        email=email,
        name=student["name"],
        state=state,
        active_application=active,
        applications=[
            ApplicationSummary(**{k: v for k, v in a.items() if k != "student_email"})
            for a in applications
        ],
        unread_personal=unread_personal,
        unread_work=unread_work,
    )


@app.get("/api/v1/inbox/{email}", response_model=Inbox)
def get_inbox_endpoint(email: str, inbox: str = "personal") -> Inbox:
    """Get a student's inbox messages."""
    messages = get_inbox(email, inbox)
    return Inbox(
        inbox=inbox,
        messages=[
            Message(**{**m, "is_read": bool(m.get("is_read"))})
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

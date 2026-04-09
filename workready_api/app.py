"""FastAPI application for the WorkReady Resume Assessment API."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from workready_api.assessor import assess_stub, assess_with_llm
from workready_api.jobs import get_job_description, load_jobs
from workready_api.models import AssessmentResult
from workready_api.pdf import extract_text

SITE_SLUGS = [
    "nexuspoint-systems",
    "ironvale-resources",
    "meridian-advisory",
    "metro-council-wa",
    "southern-cross-financial",
    "horizon-foundation",
]

app = FastAPI(
    title="WorkReady Resume Assessment API",
    version="0.1.0",
    description="Receives resume submissions and returns structured feedback.",
)

# Allow CORS from all GitHub Pages origins and localhost
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://michael-borck.github.io",
        "http://localhost:8080",
        "http://127.0.0.1:8080",
    ],
    allow_methods=["POST", "GET"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup_load_jobs() -> None:
    """Load job data on startup."""
    sites_dir = Path(os.environ.get("SITES_DIR", str(Path(__file__).parent.parent.parent)))
    load_jobs(sites_dir, SITE_SLUGS)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


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
    """Receive a resume submission and return assessment feedback."""
    # Extract text from uploaded PDF
    pdf_bytes = await resume.read()
    resume_text = extract_text(pdf_bytes)

    # Look up the job description for comparison
    job_description = get_job_description(company_slug, job_slug)

    # Assess — use LLM if available, otherwise stub
    use_llm = os.environ.get("USE_LLM", "").lower() in ("1", "true", "yes")

    if use_llm:
        result = await assess_with_llm(
            resume_text=resume_text,
            cover_letter=cover_letter,
            job_title=job_title,
            job_description=job_description,
        )
    else:
        result = assess_stub(
            resume_text=resume_text,
            cover_letter=cover_letter,
            job_title=job_title,
            job_description=job_description,
        )

    return result

"""PDF text extraction and contact-detail redaction using PyMuPDF."""

from __future__ import annotations

import re
from pathlib import Path
import secrets

import fitz  # PyMuPDF
from fastapi import HTTPException

# --- Contact-detail redaction ------------------------------------------------
# Students may upload their real resume. The simulation only needs the
# professional content (skills, experience, tone) — never anyone's contact
# details. Matching identifiers are filtered before processing. This is
# best-effort data minimisation, not a guarantee of anonymity.

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

_PHONE_RE = re.compile(
    # Australian formats only — the simulation is WA-set, and a looser
    # generic pattern would eat date ranges like 2019-2021.
    r"(?<!\d)(?:"
    r"\+?61[\s\-]?\(?\d\)?(?:[\s\-]?\d){8}"  # AU intl: +61 then 9 digits, any grouping
    r"|\(?(?:0[2-9])\)?(?:[\s\-]?\d){8}"     # AU local: 0X + 8 digits (0412…, (08)…)
    r")(?!\d)"
)

_URL_RE = re.compile(
    r"https?://\S+|www\.\S+|(?:linkedin\.com|github\.com|githubusercontent\.com)/\S+",
    re.IGNORECASE,
)

_ADDRESS_LINE_RE = re.compile(
    r"^[ \t]*(?:name|full name|date of birth|dob|passport|student id|address|street addr(?:ess)?|location)[ \t]*[:\-].*$",
    re.IGNORECASE | re.MULTILINE,
)


def redact_contact_details(text: str) -> str:
    """Strip contact identifiers from extracted resume text.

    Removes email addresses, phone numbers, web links, and explicitly
    labelled address lines. Professional content is left untouched —
    assessors and reviewers only need skills and experience.
    """
    text = _URL_RE.sub("[link removed]", text)
    text = _EMAIL_RE.sub("[email removed]", text)
    text = _PHONE_RE.sub("[phone removed]", text)
    text = _ADDRESS_LINE_RE.sub("[address removed]", text)
    text = re.sub(r"\+\d[\d ()\-]{8,}\d", "[phone removed]", text)
    return text


def extract_text(pdf_bytes: bytes) -> str:
    """Extract plain text from a PDF file's bytes."""
    if len(pdf_bytes) > 5 * 1024 * 1024:
        raise HTTPException(413, "PDF must be no larger than 5 MB.")
    if not pdf_bytes.lstrip().startswith(b'%PDF-'):
        raise HTTPException(400, "Please upload a PDF document.")
    try:
        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            if doc.needs_pass or doc.page_count > 20:
                raise HTTPException(400, "Use an unlocked PDF of at most 20 pages.")
            text = "\n".join(page.get_text() for page in doc).strip()
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(400, "The PDF could not be read.")
    if not text:
        raise HTTPException(400, "No readable text found. Please use a text-based PDF.")
    return text[:50000]


def store_filtered_pdf(content: bytes, directory: Path) -> Path:
    """Keep a rebuilt, filtered text PDF, not the original upload or metadata."""
    import textwrap
    text = redact_contact_details(extract_text(content))
    directory.mkdir(parents=True, exist_ok=True)
    output = directory / (secrets.token_hex(16) + '.pdf')
    with fitz.open() as doc:
        page, y = doc.new_page(), 50
        for paragraph in text.splitlines():
            for line in textwrap.wrap(paragraph, width=85) or ['']:
                if y > 790:
                    page, y = doc.new_page(), 50
                page.insert_text((40, y), line, fontsize=10)
                y += 14
        doc.save(output)
    return output

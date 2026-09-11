"""PDF text extraction and contact-detail redaction using PyMuPDF."""

from __future__ import annotations

import re

import fitz  # PyMuPDF

# --- Contact-detail redaction ------------------------------------------------
# Students may upload their real resume. The simulation only needs the
# professional content (skills, experience, tone) — never anyone's contact
# details. These are stripped at import so stored resume text (shown in
# journey reports, fed to assessors/reviewers) can't carry real PII even
# when the uploaded document does.

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
    r"^\s*(?:address|street addr(?:ess)?|location)\s*[:\-].*$",
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
    return text


def extract_text(pdf_bytes: bytes) -> str:
    """Extract plain text from a PDF file's bytes."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pages = []
    for page in doc:
        pages.append(page.get_text())
    doc.close()
    return "\n".join(pages).strip()

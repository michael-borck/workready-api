"""PDF text extraction using PyMuPDF."""

from __future__ import annotations

import fitz  # PyMuPDF


def extract_text(pdf_bytes: bytes) -> str:
    """Extract plain text from a PDF file's bytes."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pages = []
    for page in doc:
        pages.append(page.get_text())
    doc.close()
    return "\n".join(pages).strip()

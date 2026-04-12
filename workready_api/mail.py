"""In-app email endpoints — compose, send, reply, delete, sent box.

Students can compose messages to any address in the simulation. Valid
addresses (characters + generic company addresses) are delivered; invalid
addresses bounce back to the student's inbox with a "did you mean?"
suggestion if possible.

Attachments (PDF) are stored on disk alongside the SQLite database.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from workready_api.db import (
    create_attachment,
    create_bounce_message,
    create_message,
    create_outbound_message,
    get_attachments,
    get_message,
    get_sent_messages,
    get_student_by_email,
    get_thread,
    soft_delete_message,
)
from workready_api.email_registry import (
    SYSTEM_NOREPLY,
    find_closest_match,
    resolve_address,
)

router = APIRouter(prefix="/api/v1/mail", tags=["mail"])

ATTACHMENTS_DIR = Path(
    os.environ.get("WORKREADY_ATTACHMENTS_DIR", "")
) or Path(os.environ.get("WORKREADY_DB", "workready.db")).parent / "attachments"

MAX_ATTACHMENT_SIZE = 5 * 1024 * 1024  # 5 MB


# --- Models ---


class ComposeRequest(BaseModel):
    """Compose a new message."""
    student_email: str
    recipient_email: str
    subject: str
    body: str


class ReplyRequest(BaseModel):
    """Reply to a message."""
    student_email: str
    body: str


class SentMessage(BaseModel):
    """An outbound message in the sent box."""
    id: int
    recipient_email: str
    subject: str
    body: str
    status: str  # delivered | bounced
    has_attachment: bool
    thread_id: int | None
    created_at: str


class SentBox(BaseModel):
    """The sent box contents."""
    messages: list[SentMessage]
    total: int


class SendResult(BaseModel):
    """Result of sending a message."""
    message_id: int
    status: str  # delivered | bounced
    bounce_reason: str | None = None
    suggestion: str | None = None


class AttachmentInfo(BaseModel):
    """Attachment metadata."""
    id: int
    filename: str
    content_type: str
    file_size: int


# --- Endpoints ---


@router.post("/compose", response_model=SendResult)
async def compose_message(
    student_email: str = Form(...),
    recipient_email: str = Form(...),
    subject: str = Form(...),
    body: str = Form(""),
    attachment: UploadFile | None = File(None),
) -> SendResult:
    """Compose and send a new message.

    If recipient_email is valid in the simulation → delivered.
    If invalid → bounced (with a 'did you mean?' suggestion).
    If recipient is the system noreply → bounced with explanation.
    """
    student = get_student_by_email(student_email)
    if not student:
        raise HTTPException(404, "Student not found — sign in first")

    recipient_email = recipient_email.strip().lower()
    subject = subject.strip()
    if not subject:
        subject = "(no subject)"

    # Handle attachment
    has_attachment = False
    attachment_path = None
    attachment_size = 0

    if attachment and attachment.filename:
        content = await attachment.read()
        attachment_size = len(content)
        if attachment_size > MAX_ATTACHMENT_SIZE:
            raise HTTPException(413, "Attachment too large (max 5MB)")

        has_attachment = True
        # Store in data/attachments/{student_id}/{filename}
        student_dir = ATTACHMENTS_DIR / str(student["id"])
        student_dir.mkdir(parents=True, exist_ok=True)
        # Sanitise filename
        safe_name = Path(attachment.filename).name
        attachment_path = student_dir / safe_name
        attachment_path.write_bytes(content)

    # Resolve the recipient
    resolved = resolve_address(recipient_email)

    if resolved is None:
        # Invalid address → bounce
        suggestion = find_closest_match(recipient_email)

        # Still record the outbound message in the sent box (as bounced)
        msg_id = create_outbound_message(
            student_id=student["id"],
            student_email=student_email,
            recipient_email=recipient_email,
            subject=subject,
            body=body,
            has_attachment=has_attachment,
            status="bounced",
        )

        if has_attachment and attachment_path:
            create_attachment(
                message_id=msg_id,
                filename=attachment.filename or "attachment.pdf",
                file_path=str(attachment_path),
                file_size=attachment_size,
            )

        # Create bounce notification in inbox
        create_bounce_message(
            student_id=student["id"],
            student_email=student_email,
            original_recipient=recipient_email,
            original_subject=subject,
            suggestion=suggestion,
        )

        return SendResult(
            message_id=msg_id,
            status="bounced",
            bounce_reason=f"Address not found: {recipient_email}",
            suggestion=suggestion,
        )

    if resolved.kind == "system":
        # Can't reply to noreply — bounce with explanation
        msg_id = create_outbound_message(
            student_id=student["id"],
            student_email=student_email,
            recipient_email=recipient_email,
            subject=subject,
            body=body,
            status="bounced",
        )
        create_message(
            student_id=student["id"],
            student_email=student_email,
            sender_name="Mail Delivery System",
            subject=f"Cannot reply to: {recipient_email}",
            body=(
                "This is an automated system address and does not accept replies.\n\n"
                "To contact a company, use their direct email address — you can "
                "find it on their website's contact page."
            ),
            inbox="personal",
        )
        return SendResult(
            message_id=msg_id,
            status="bounced",
            bounce_reason="This is a no-reply address",
        )

    # Valid address → delivered
    msg_id = create_outbound_message(
        student_id=student["id"],
        student_email=student_email,
        recipient_email=recipient_email,
        subject=subject,
        body=body,
        has_attachment=has_attachment,
        status="delivered",
    )

    if has_attachment and attachment_path:
        create_attachment(
            message_id=msg_id,
            filename=attachment.filename or "attachment.pdf",
            file_path=str(attachment_path),
            file_size=attachment_size,
        )

    # Send auto-acknowledgment from the recipient
    if resolved.kind == "character":
        _send_character_ack(student, resolved, subject)
    elif resolved.kind == "generic":
        _send_generic_ack(student, resolved, subject)

    return SendResult(message_id=msg_id, status="delivered")


@router.post("/reply/{message_id}", response_model=SendResult)
async def reply_to_message(
    message_id: int,
    student_email: str = Form(...),
    body: str = Form(...),
    attachment: UploadFile | None = File(None),
) -> SendResult:
    """Reply to a received message.

    Pre-fills the recipient from the original sender_email and threads
    the conversation via thread_id.
    """
    student = get_student_by_email(student_email)
    if not student:
        raise HTTPException(404, "Student not found")

    original = get_message(message_id)
    if not original:
        raise HTTPException(404, "Original message not found")
    if original["student_id"] != student["id"]:
        raise HTTPException(403, "Not your message")

    # Determine who we're replying to
    sender_email = original.get("sender_email") or SYSTEM_NOREPLY
    subject = original.get("subject", "")
    if not subject.startswith("Re: "):
        subject = f"Re: {subject}"

    # Use the original's thread_id, or the original's own id if no thread yet
    thread_id = original.get("thread_id") or original["id"]

    # Handle attachment
    has_attachment = False
    attachment_path = None
    attachment_size = 0
    if attachment and attachment.filename:
        content = await attachment.read()
        attachment_size = len(content)
        if attachment_size > MAX_ATTACHMENT_SIZE:
            raise HTTPException(413, "Attachment too large (max 5MB)")
        has_attachment = True
        student_dir = ATTACHMENTS_DIR / str(student["id"])
        student_dir.mkdir(parents=True, exist_ok=True)
        safe_name = Path(attachment.filename).name
        attachment_path = student_dir / safe_name
        attachment_path.write_bytes(content)

    # Resolve and send via the compose logic
    resolved = resolve_address(sender_email)

    if resolved is None or resolved.kind == "system":
        # Bounce — can't reply to this address
        msg_id = create_outbound_message(
            student_id=student["id"],
            student_email=student_email,
            recipient_email=sender_email,
            subject=subject,
            body=body,
            thread_id=thread_id,
            status="bounced",
        )
        reason = (
            "This is an automated system address and does not accept replies."
            if resolved and resolved.kind == "system"
            else f"Address not found: {sender_email}"
        )
        create_message(
            student_id=student["id"],
            student_email=student_email,
            sender_name="Mail Delivery System",
            subject=f"Reply failed: {sender_email}",
            body=f"{reason}\n\nTo contact a company, use their direct email "
                 "address — you can find it on their website's contact page.",
            inbox="personal",
        )
        return SendResult(
            message_id=msg_id,
            status="bounced",
            bounce_reason=reason,
        )

    # Delivered
    msg_id = create_outbound_message(
        student_id=student["id"],
        student_email=student_email,
        recipient_email=sender_email,
        subject=subject,
        body=body,
        thread_id=thread_id,
        has_attachment=has_attachment,
        status="delivered",
    )

    if has_attachment and attachment_path:
        create_attachment(
            message_id=msg_id,
            filename=attachment.filename or "attachment.pdf",
            file_path=str(attachment_path),
            file_size=attachment_size,
        )

    if resolved.kind == "character":
        _send_character_ack(student, resolved, subject, thread_id)
    elif resolved.kind == "generic":
        _send_generic_ack(student, resolved, subject, thread_id)

    return SendResult(message_id=msg_id, status="delivered")


@router.get("/sent/{email}", response_model=SentBox)
def get_sent_box(email: str) -> SentBox:
    """Get the student's sent messages."""
    student = get_student_by_email(email)
    if not student:
        raise HTTPException(404, "Student not found")

    msgs = get_sent_messages(student["id"])
    return SentBox(
        messages=[
            SentMessage(
                id=m["id"],
                recipient_email=m.get("recipient_email", ""),
                subject=m.get("subject", ""),
                body=m.get("body", ""),
                status=m.get("status", "delivered"),
                has_attachment=bool(m.get("has_attachment")),
                thread_id=m.get("thread_id"),
                created_at=m.get("created_at", ""),
            )
            for m in msgs
        ],
        total=len(msgs),
    )


@router.delete("/message/{message_id}")
def delete_message(message_id: int, student_email: str) -> dict:
    """Soft-delete a message."""
    student = get_student_by_email(student_email)
    if not student:
        raise HTTPException(404, "Student not found")

    msg = get_message(message_id)
    if not msg:
        raise HTTPException(404, "Message not found")
    if msg["student_id"] != student["id"]:
        raise HTTPException(403, "Not your message")

    soft_delete_message(message_id)
    return {"deleted": True, "message_id": message_id}


@router.get("/thread/{thread_id}")
def get_conversation_thread(thread_id: int, student_email: str) -> dict:
    """Get all messages in a thread (both inbound and outbound)."""
    student = get_student_by_email(student_email)
    if not student:
        raise HTTPException(404, "Student not found")

    messages = get_thread(thread_id, student["id"])
    return {"thread_id": thread_id, "messages": messages}


@router.get("/attachments/{message_id}")
def get_message_attachments(message_id: int, student_email: str) -> dict:
    """Get attachment metadata for a message."""
    student = get_student_by_email(student_email)
    if not student:
        raise HTTPException(404, "Student not found")

    msg = get_message(message_id)
    if not msg or msg["student_id"] != student["id"]:
        raise HTTPException(404, "Message not found")

    attachments = get_attachments(message_id)
    return {
        "message_id": message_id,
        "attachments": [
            AttachmentInfo(
                id=a["id"],
                filename=a["filename"],
                content_type=a["content_type"],
                file_size=a["file_size"],
            ).model_dump()
            for a in attachments
        ],
    }


@router.get("/directory")
def get_email_directory() -> dict:
    """List all valid email addresses in the simulation.

    Exposed so the portal can offer autocomplete or a directory view.
    """
    from workready_api.email_registry import get_registry

    registry = get_registry()
    entries = []
    for addr in registry.values():
        entry = {
            "email": addr.email,
            "kind": addr.kind,
            "company_slug": addr.company_slug,
        }
        if addr.character_name:
            entry["name"] = addr.character_name
            entry["role"] = addr.character_role
        entries.append(entry)

    # Sort: characters first, then generic, then system
    kind_order = {"character": 0, "generic": 1, "system": 2}
    entries.sort(key=lambda e: (kind_order.get(e["kind"], 9), e["email"]))

    return {"addresses": entries, "total": len(entries)}


# --- Internal helpers ---


def _send_character_ack(
    student: dict,
    resolved,
    subject: str,
    thread_id: int | None = None,
):
    """Send an acknowledgment from a character.

    In Phase F2b this will be replaced by an LLM-generated reply.
    For now, send a simple acknowledgment.
    """
    name = resolved.character_name or "Staff member"
    role = resolved.character_role or ""
    company = resolved.company_slug or ""

    body = (
        f"Thank you for your message. I've received it and will "
        f"respond as soon as I can.\n\n"
        f"Best regards,\n{name}"
    )

    create_message(
        student_id=student["id"],
        student_email=student["email"],
        sender_name=name,
        sender_role=role,
        sender_email=resolved.email,
        subject=f"Re: {subject}" if not subject.startswith("Re:") else subject,
        body=body,
        inbox="personal",
        thread_id=thread_id,
    )


def _send_generic_ack(
    student: dict,
    resolved,
    subject: str,
    thread_id: int | None = None,
):
    """Send an auto-acknowledgment from a generic company address."""
    company = resolved.company_slug or "the company"
    # Human-readable company name from the slug
    company_name = company.replace("-", " ").title()

    body = (
        f"Thank you for your enquiry. Your message has been received "
        f"by {company_name}.\n\n"
        f"A member of our team will respond within 2 business days.\n\n"
        f"This is an automated acknowledgment."
    )

    create_message(
        student_id=student["id"],
        student_email=student["email"],
        sender_name=company_name,
        sender_role="Auto-reply",
        sender_email=resolved.email,
        subject=f"Re: {subject}" if not subject.startswith("Re:") else subject,
        body=body,
        inbox="personal",
        thread_id=thread_id,
    )

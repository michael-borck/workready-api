"""Comms monitor — classifier for outgoing student messages.

One LLM call per outgoing message. Scores three axes:

- recipient_appropriateness: was this the right person to contact?
- tone: is the language appropriate for a professional context?
- channel_appropriateness: is the right channel being used?

Returns a ClassificationResult dataclass. The caller (mail.py or the
chat send route) decides what to do with a flag — the classifier just
scores.

Fail-open: any exception (provider down, malformed JSON, etc.) returns
a classifier_unavailable result with all axes set to "ok". The message
flows normally. The rationale: a broken safety layer should never
block a legitimate student message.

Stub mode: when LLM_PROVIDER=stub, returns all-ok with
rationale="stub mode" without making any network call.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Literal

from workready_api.interview import chat_completion


logger = logging.getLogger(__name__)

RecipientFlag = Literal["ok", "wrong_audience"]
ToneFlag = Literal["ok", "sharp", "inappropriate"]
ChannelFlag = Literal["ok", "wrong_channel"]


@dataclass
class ClassificationResult:
    recipient_appropriateness: RecipientFlag = "ok"
    tone: ToneFlag = "ok"
    channel_appropriateness: ChannelFlag = "ok"
    rationale: str = ""
    classified_at: str = ""
    status: str = "ok"  # "ok" | "classifier_unavailable"

    def any_flag(self) -> bool:
        return (
            self.recipient_appropriateness != "ok"
            or self.tone != "ok"
            or self.channel_appropriateness != "ok"
        )

    def to_json(self) -> str:
        return json.dumps(asdict(self))


_SYSTEM_PROMPT = """You are a communications quality assistant for a workplace internship simulation. You classify outgoing student messages on three axes.

Students are interns at one of six companies. They communicate with:
- Their immediate team (chat + email)
- Wider organisation characters including executive leadership (email only)
- The company careers desk (anonymous, pre-hire)

Your job is to identify messages that would benefit from a gentle redirect, NOT to block every flaw. Only flag clear cases where a realistic workplace mentor would step in.

For each message, score three axes:

recipient_appropriateness:
- "ok" — the recipient is a reasonable choice for this request
- "wrong_audience" — the student is asking something of the wrong person (e.g. asking the CEO about where the printer is, or asking an individual contributor for cross-departmental policy decisions)

tone:
- "ok" — professional or professionally-casual; fine for workplace
- "sharp" — notably terse, abrupt, or mildly aggressive, but not crossing a line
- "inappropriate" — rude, hostile, personal, or clearly crossing a professional line

channel_appropriateness:
- "ok" — the channel (personal vs work email) matches the purpose
- "wrong_channel" — using personal email for an obviously-work matter (or vice versa)

Return ONLY a JSON object with these exact keys. No markdown fences, no commentary:
{
  "recipient_appropriateness": "ok" | "wrong_audience",
  "tone": "ok" | "sharp" | "inappropriate",
  "channel_appropriateness": "ok" | "wrong_channel",
  "rationale": "one sentence explaining any flag, or empty string if all ok"
}"""


async def classify_outgoing(
    *,
    student_id: int,
    application_id: int,
    channel: str,
    recipient: str,
    subject: str,
    body: str,
    student_stage: str = "",
    recipient_role_hint: str = "",
) -> ClassificationResult:
    """Classify an outgoing student message on three axes.

    Returns a ClassificationResult. On any error, fails open with
    `status="classifier_unavailable"`.
    """
    classified_at = datetime.now(timezone.utc).isoformat()

    # Stub short-circuit
    if os.environ.get("LLM_PROVIDER", "stub").lower() == "stub":
        return ClassificationResult(
            rationale="stub mode",
            classified_at=classified_at,
        )

    user_prompt = _build_user_prompt(
        channel=channel,
        recipient=recipient,
        subject=subject,
        body=body,
        student_stage=student_stage,
        recipient_role_hint=recipient_role_hint,
    )

    try:
        raw = await chat_completion(
            _SYSTEM_PROMPT,
            [{"role": "user", "content": user_prompt}],
        )
        return _parse_classification(raw, classified_at)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "classify_outgoing: classifier unavailable (%s)", exc,
        )
        return ClassificationResult(
            rationale=f"classifier_unavailable: {exc}",
            classified_at=classified_at,
            status="classifier_unavailable",
        )


def _build_user_prompt(
    *,
    channel: str,
    recipient: str,
    subject: str,
    body: str,
    student_stage: str,
    recipient_role_hint: str,
) -> str:
    parts = [
        f"Channel: {channel}",
        f"Recipient: {recipient}",
    ]
    if recipient_role_hint:
        parts.append(f"Recipient role: {recipient_role_hint}")
    if student_stage:
        parts.append(f"Student stage: {student_stage}")
    if subject:
        parts.append(f"Subject: {subject}")
    parts.append(f"Body:\n{body}")
    parts.append("\nClassify this message. Return ONLY the JSON object.")
    return "\n".join(parts)


def _parse_classification(raw: str, classified_at: str) -> ClassificationResult:
    """Parse the classifier's JSON response, fail-open on any error."""
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
        return ClassificationResult(
            recipient_appropriateness=_coerce_flag(
                data.get("recipient_appropriateness"),
                valid={"ok", "wrong_audience"},
            ),
            tone=_coerce_flag(
                data.get("tone"),
                valid={"ok", "sharp", "inappropriate"},
            ),
            channel_appropriateness=_coerce_flag(
                data.get("channel_appropriateness"),
                valid={"ok", "wrong_channel"},
            ),
            rationale=str(data.get("rationale", ""))[:500],
            classified_at=classified_at,
        )
    except (ValueError, TypeError) as exc:
        logger.warning(
            "_parse_classification: bad response (%s) raw=%r", exc, raw[:200],
        )
        return ClassificationResult(
            rationale=f"malformed_response: {exc}",
            classified_at=classified_at,
            status="classifier_unavailable",
        )


def _coerce_flag(value, *, valid: set[str]) -> str:
    """Coerce an unknown value to 'ok'. Fail-open on any unknown flag."""
    if isinstance(value, str) and value in valid:
        return value
    return "ok"

#!/usr/bin/env python3
"""Smoke test: comms_monitor classifier.

Covers:
1. Stub mode returns all-ok with rationale='stub mode'
2. any_flag() returns False when all axes are ok
3. any_flag() returns True when at least one axis is flagged
4. to_json() produces valid JSON
5. Fail-open via patched chat_completion raising an exception
"""

import asyncio
import json
import os

os.environ.setdefault("LLM_PROVIDER", "stub")

from workready_api.comms_monitor import (
    ClassificationResult, classify_outgoing, _parse_classification,
)

# --- Test 1: stub mode ---
result = asyncio.run(classify_outgoing(
    student_id=1, application_id=1, channel="chat",
    recipient="karen@test.com", subject="", body="hey karen",
))
assert result.recipient_appropriateness == "ok"
assert result.tone == "ok"
assert result.channel_appropriateness == "ok"
assert result.rationale == "stub mode"
assert result.status == "ok"
print("  [1/5] stub mode returns all-ok")

# --- Test 2: any_flag is False for all-ok ---
assert result.any_flag() is False
print("  [2/5] any_flag() false for all-ok")

# --- Test 3: any_flag is True with a flag ---
flagged = ClassificationResult(
    recipient_appropriateness="wrong_audience",
    rationale="CEO about trivia",
)
assert flagged.any_flag() is True
print("  [3/5] any_flag() true when flagged")

# --- Test 4: to_json round-trip ---
js = flagged.to_json()
data = json.loads(js)
assert data["recipient_appropriateness"] == "wrong_audience"
print("  [4/5] to_json is valid JSON")

# --- Test 5: fail-open via parse error ---
bad = _parse_classification("not-json-at-all", "2026-04-14T10:00:00Z")
assert bad.status == "classifier_unavailable"
assert bad.recipient_appropriateness == "ok"
print("  [5/5] fail-open on bad JSON")

# --- Test 6: coerce unknown flag values to ok ---
unknown = _parse_classification('{"recipient_appropriateness": "WEIRD_VALUE"}', "2026-04-14T10:00:00Z")
assert unknown.recipient_appropriateness == "ok"
print("  [6/6] unknown flag values coerce to ok")

print("\nOK: comms_monitor smoke passed")

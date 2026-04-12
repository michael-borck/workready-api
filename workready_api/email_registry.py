"""Email registry — valid simulation email addresses and their owners.

Builds a map of every valid email address in the simulation from the
loaded company/employee data. Used by the compose/send system to decide
whether a student's outbound message is delivered or bounced.

Three sender tiers:
  1. system   — noreply@workready.eduserver.au (no replies)
  2. character — first.last@companydomain.com.au (LLM replies)
  3. generic  — careers@, info@, etc. (auto-ack or application pipeline)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from workready_api.jobs import _JOB_CACHE, _COMPANY_CACHE


# --- Constants ---

SYSTEM_NOREPLY = "noreply@workready.eduserver.au"

# Company slug → email domain. Mirrors the CNAME / site domains.
COMPANY_DOMAINS: dict[str, str] = {
    "ironvale-resources": "ironvaleresources.com.au",
    "nexuspoint-systems": "nexuspointsystems.com.au",
    "horizon-foundation": "horizonfoundation.org.au",
    "southern-cross-financial": "southerncrossfinancial.com.au",
    "metro-council-wa": "metrocouncilwa.gov.au",
    "meridian-advisory": "meridianadvisory.com.au",
}

# Generic role addresses per company. Each maps to a handler type.
# "careers" → routed to application pipeline if attachment present
# "info"    → auto-ack, optionally routed to a character
GENERIC_PREFIXES: dict[str, str] = {
    "info": "generic",
    "careers": "careers",
    "enquiries": "generic",
    "hello": "generic",
    "council": "generic",
    "give": "generic",
    "noc": "generic",
}


@dataclass
class RegisteredAddress:
    """A valid email address in the simulation."""

    email: str
    kind: str  # "system" | "character" | "generic"
    company_slug: str | None = None
    character_slug: str | None = None
    character_name: str | None = None
    character_role: str | None = None
    handler: str = "default"  # "default" | "careers" | "noreply"


# --- Registry ---

_REGISTRY: dict[str, RegisteredAddress] = {}


def _slug_to_email_local(name: str) -> str:
    """Convert a character name to an email local part.

    'Karen Whitfield' → 'karen.whitfield'
    'Dr. Ravi Mehta'  → 'ravi.mehta'
    """
    # Strip honorifics
    clean = re.sub(r"^(Dr\.?|Prof\.?|Mr\.?|Mrs\.?|Ms\.?)\s+", "", name.strip())
    parts = clean.lower().split()
    return ".".join(parts[:2])  # first.last


def build_registry() -> dict[str, RegisteredAddress]:
    """Build the email registry from loaded job/company data.

    Call this after load_jobs() has populated the caches.
    Returns the registry dict (also stored in module-level _REGISTRY).
    """
    global _REGISTRY
    _REGISTRY.clear()

    # 1. System noreply
    _REGISTRY[SYSTEM_NOREPLY] = RegisteredAddress(
        email=SYSTEM_NOREPLY,
        kind="system",
        handler="noreply",
    )

    # 2. Character emails — derived from brief.yaml employees via jobs.json
    #    The employee data is baked into the job cache as reports_to / manager info,
    #    but we need the full employee list. We'll parse it from the company cache
    #    or from the brief data that load_jobs stores.
    #
    #    Since the current jobs.py only caches jobs and company-level meta (not
    #    employees), we build character emails from what we know:
    #    - Every reports_to field in jobs.json names a person
    #    - We also check the manager_persona field for the slug
    #
    #    For a richer registry, we'd load brief.yaml directly. For now, this
    #    gets us the key characters (the ones students would actually email).

    seen_characters: set[str] = set()

    for (company_slug, job_slug), job in _JOB_CACHE.items():
        domain = COMPANY_DOMAINS.get(company_slug)
        if not domain:
            continue

        # The reports_to field names a person (e.g. "Pete Drummond")
        reports_to = job.get("reports_to", "")
        if reports_to and reports_to not in seen_characters:
            seen_characters.add(reports_to)
            local = _slug_to_email_local(reports_to)
            email = f"{local}@{domain}"
            slug = local.replace(".", "-")
            _REGISTRY[email] = RegisteredAddress(
                email=email,
                kind="character",
                company_slug=company_slug,
                character_slug=slug,
                character_name=reports_to,
                character_role=job.get("manager_role", ""),
            )

    # 3. Generic addresses for every company
    for company_slug, domain in COMPANY_DOMAINS.items():
        for prefix, handler in GENERIC_PREFIXES.items():
            email = f"{prefix}@{domain}"
            if email not in _REGISTRY:
                _REGISTRY[email] = RegisteredAddress(
                    email=email,
                    kind="generic",
                    company_slug=company_slug,
                    handler=handler,
                )

    return _REGISTRY


def get_registry() -> dict[str, RegisteredAddress]:
    """Get the current email registry. Builds it if empty."""
    if not _REGISTRY:
        build_registry()
    return _REGISTRY


def resolve_address(email: str) -> RegisteredAddress | None:
    """Look up an email address. Returns None if not found (= bounce)."""
    registry = get_registry()
    return registry.get(email.strip().lower())


def find_closest_match(email: str) -> str | None:
    """Find the closest valid address for a 'did you mean?' suggestion.

    Simple: check if swapping one character in the local part matches
    a known address at the same domain. Returns the suggestion or None.
    """
    email = email.strip().lower()
    if "@" not in email:
        return None

    local, domain = email.rsplit("@", 1)
    registry = get_registry()

    # Find all addresses at the same domain
    candidates = [
        addr.email for addr in registry.values()
        if addr.email.endswith("@" + domain) and addr.email != email
    ]

    if not candidates:
        return None

    # Proper Levenshtein distance on the local part
    def _levenshtein(s1: str, s2: str) -> int:
        if len(s1) < len(s2):
            return _levenshtein(s2, s1)
        if len(s2) == 0:
            return len(s1)
        prev = list(range(len(s2) + 1))
        for i, c1 in enumerate(s1):
            curr = [i + 1]
            for j, c2 in enumerate(s2):
                curr.append(min(
                    prev[j + 1] + 1,      # deletion
                    curr[j] + 1,           # insertion
                    prev[j] + (c1 != c2),  # substitution
                ))
            prev = curr
        return prev[-1]

    best = None
    best_dist = 999

    for candidate in candidates:
        c_local = candidate.rsplit("@", 1)[0]
        dist = _levenshtein(local, c_local)
        if dist < best_dist and dist <= 3:
            best_dist = dist
            best = candidate

    return best


def list_addresses_for_company(company_slug: str) -> list[RegisteredAddress]:
    """List all valid addresses for a company (character + generic)."""
    registry = get_registry()
    return [
        addr for addr in registry.values()
        if addr.company_slug == company_slug
    ]

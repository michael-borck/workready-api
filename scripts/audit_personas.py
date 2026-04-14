#!/usr/bin/env python3
"""Audit persona-file completeness across every company.

Scans each company's jobs.json + brief.yaml and reports:
1. Characters referenced in `team:` field of any job but missing a
   content/employees/<slug>-prompt.txt file
2. Characters referenced in `reports_to` on any job but missing a
   prompt file
3. Characters in brief.yaml employees[] but missing a prompt file

Usage:
    uv run python scripts/audit_personas.py
    uv run python scripts/audit_personas.py --fail-on-gaps  # exit 1 if any
"""

import json
import os
import re
import sys
from pathlib import Path

# The parent of loco-ensyo/workready-api is loco-ensyo/
SITES_DIR = Path(os.environ.get(
    "SITES_DIR",
    str(Path(__file__).parent.parent.parent),
))

COMPANIES = [
    "ironvale-resources",
    "nexuspoint-systems",
    "meridian-advisory",
    "metro-council-wa",
    "southern-cross-financial",
    "horizon-foundation",
]


def slugify(name: str) -> str:
    """Match workready_api.lunchroom._slugify_name."""
    s = re.sub(r"[^\w\s-]", "", name.lower())
    s = re.sub(r"\s+", "-", s).strip("-")
    return s or "colleague"


def load_jobs(company_dir: Path) -> dict:
    path = company_dir / "jobs.json"
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print(
            f"WARNING: could not read {path}: {exc}",
            file=sys.stderr,
        )
        return {}


def audit_company(slug: str) -> list[dict]:
    """Return a list of gap records for this company."""
    company_dir = SITES_DIR / slug
    jobs_data = load_jobs(company_dir)
    employees_dir = company_dir / "content" / "employees"

    existing_files = set()
    if employees_dir.is_dir():
        existing_files = {
            p.stem.replace("-prompt", "")
            for p in employees_dir.glob("*-prompt.txt")
        }

    gaps: list[dict] = []

    # Source 1: team: field on each job
    for job in jobs_data.get("jobs", []):
        for char_slug in job.get("team", []):
            if char_slug not in existing_files:
                gaps.append({
                    "company": slug,
                    "character_slug": char_slug,
                    "source": f"job.{job['slug']}.team",
                    "suggested_name": char_slug.replace("-", " ").title(),
                })

    # Source 2: reports_to on each job
    for job in jobs_data.get("jobs", []):
        reports_to = job.get("reports_to", "")
        if reports_to:
            char_slug = slugify(reports_to)
            if char_slug not in existing_files:
                gaps.append({
                    "company": slug,
                    "character_slug": char_slug,
                    "source": f"job.{job['slug']}.reports_to",
                    "suggested_name": reports_to,
                })

    # Source 3: employees[] in jobs.json (from brief.yaml passthrough)
    for emp in jobs_data.get("employees", []):
        char_slug = emp.get("slug") or slugify(emp.get("name", ""))
        if char_slug and char_slug not in existing_files:
            gaps.append({
                "company": slug,
                "character_slug": char_slug,
                "source": "employees[]",
                "suggested_name": emp.get("name", char_slug),
                "role": emp.get("role", ""),
            })

    # Dedupe by (company, character_slug)
    seen = set()
    unique_gaps = []
    for g in gaps:
        key = (g["company"], g["character_slug"])
        if key in seen:
            continue
        seen.add(key)
        unique_gaps.append(g)

    return unique_gaps


def main() -> int:
    fail_on_gaps = "--fail-on-gaps" in sys.argv
    all_gaps: list[dict] = []

    print(f"Auditing persona completeness in {SITES_DIR}\n")
    for slug in COMPANIES:
        company_dir = SITES_DIR / slug
        if not company_dir.is_dir():
            print(f"  {slug}: (directory not found, skipping)")
            continue
        gaps = audit_company(slug)
        existing = len(list((company_dir / "content" / "employees").glob("*-prompt.txt"))) \
            if (company_dir / "content" / "employees").is_dir() else 0
        print(f"  {slug}: {existing} persona(s) present, {len(gaps)} gap(s)")
        for g in gaps:
            extra = f" role='{g.get('role', '')}'" if g.get("role") else ""
            print(f"    - {g['character_slug']} (suggested: '{g['suggested_name']}'){extra}")
            print(f"      referenced by: {g['source']}")
        all_gaps.extend(gaps)

    print()
    if all_gaps:
        print(f"TOTAL GAPS: {len(all_gaps)}")
        if fail_on_gaps:
            return 1
    else:
        print("TOTAL GAPS: 0 — all personas present.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

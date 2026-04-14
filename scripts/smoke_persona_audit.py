#!/usr/bin/env python3
"""Smoke test: re-run the persona audit and assert zero gaps.

Usage:
    uv run python scripts/smoke_persona_audit.py
    scripts/smoke_persona_audit.py  # if executable

This is the regression guard. If a future change adds a character
reference without also adding the persona file, this smoke fails.
Runs `scripts/audit_personas.py --fail-on-gaps` under the repo root,
so it works regardless of the caller's current directory.
"""

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    result = subprocess.run(
        ["uv", "run", "python", "scripts/audit_personas.py", "--fail-on-gaps"],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    print(result.stdout, end="")
    if result.returncode != 0:
        if result.returncode == 1:
            print("FAIL: persona audit found gaps", file=sys.stderr)
        else:
            print(
                f"FAIL: audit script exited with code {result.returncode}",
                file=sys.stderr,
            )
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        return 1
    print("OK: zero persona gaps")
    return 0


if __name__ == "__main__":
    sys.exit(main())

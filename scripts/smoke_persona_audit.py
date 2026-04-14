#!/usr/bin/env python3
"""Smoke test: re-run the persona audit and assert zero gaps.

This is the regression guard. If a future change adds a character
reference without also adding the persona file, this smoke fails.
"""

import subprocess
import sys


def main() -> int:
    result = subprocess.run(
        ["uv", "run", "python", "scripts/audit_personas.py", "--fail-on-gaps"],
        capture_output=True,
        text=True,
    )
    print(result.stdout)
    if result.returncode != 0:
        print("FAIL: persona audit found gaps", file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        return 1
    print("OK: zero persona gaps")
    return 0


if __name__ == "__main__":
    sys.exit(main())

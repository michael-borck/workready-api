"""Region-keyed public holidays for the WorkReady simulation.

Hand-edited Python source (not YAML or JSON) so we don't introduce a
new dependency. Human-editable, syntax-checked by Python itself, and
easy to extend with more regions later.

Each region maps to a list of {date: "YYYY-MM-DD", name: str} dicts.
The availability module imports this at startup; changes require a
restart.
"""

from __future__ import annotations

PUBLIC_HOLIDAYS: dict[str, list[dict[str, str]]] = {
    "australia-wa": [
        # 2026
        {"date": "2026-01-01", "name": "New Year's Day"},
        {"date": "2026-01-26", "name": "Australia Day"},
        {"date": "2026-03-02", "name": "Labour Day (WA)"},
        {"date": "2026-04-03", "name": "Good Friday"},
        {"date": "2026-04-04", "name": "Easter Saturday"},
        {"date": "2026-04-06", "name": "Easter Monday"},
        {"date": "2026-04-25", "name": "ANZAC Day"},
        {"date": "2026-06-01", "name": "Western Australia Day"},
        {"date": "2026-09-28", "name": "King's Birthday (WA)"},
        {"date": "2026-12-25", "name": "Christmas Day"},
        {"date": "2026-12-28", "name": "Boxing Day (observed)"},
        # 2027
        {"date": "2027-01-01", "name": "New Year's Day"},
        {"date": "2027-01-26", "name": "Australia Day"},
        {"date": "2027-03-01", "name": "Labour Day (WA)"},
        {"date": "2027-03-26", "name": "Good Friday"},
        {"date": "2027-03-27", "name": "Easter Saturday"},
        {"date": "2027-03-29", "name": "Easter Monday"},
        {"date": "2027-04-26", "name": "ANZAC Day (observed)"},
        {"date": "2027-06-07", "name": "Western Australia Day"},
        {"date": "2027-09-27", "name": "King's Birthday (WA)"},
        {"date": "2027-12-27", "name": "Christmas Day (observed)"},
        {"date": "2027-12-28", "name": "Boxing Day (observed)"},
    ],
}


def holiday_dates_for_region(region: str) -> set[str]:
    """Return the set of ISO date strings for a region. Unknown → empty set."""
    entries = PUBLIC_HOLIDAYS.get(region, [])
    return {e["date"] for e in entries}

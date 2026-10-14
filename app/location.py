"""Which postings we keep: the Treasure Valley (Boise metro).

Everything else is dropped in refresh before it's stored or has details fetched.
To widen the search area, edit the patterns here.
"""

import re

# Distinctive names: a match anywhere is enough.
_UNAMBIGUOUS = r"boise|nampa|treasure\s+valley"
# Names shared with towns in other states (Meridian MS, Eagle CO, Garden City KS, ...):
# these only count when Idaho/ID appears right before or after them.
_AMBIGUOUS = r"meridian|eagle|star|kuna|caldwell|garden\s+city|middleton|emmett"
_STATE = r"id|idaho"

TREASURE_VALLEY = re.compile(
    rf"\b(?:{_UNAMBIGUOUS})\b"
    rf"|\b(?:{_AMBIGUOUS})\b[\s,\-]*\b(?:{_STATE})\b"  # "Meridian, ID"
    rf"|\b(?:{_STATE})\b[\s,\-]+\b(?:{_AMBIGUOUS})\b",  # "Idaho - Meridian" (Workday style)
    re.IGNORECASE,
)

# Location text that hides the real locations, e.g. Workday's "3 Locations".
_PLACEHOLDER = re.compile(r"^\s*\d+\s+locations\s*$", re.IGNORECASE)


def in_area(location: str | None, aliases: str | None = None) -> bool:
    """aliases: optional per-company regex (companies.location_aliases) that also counts."""
    if not location:
        return False
    if TREASURE_VALLEY.search(location):
        return True
    return bool(aliases and re.search(aliases, location, re.IGNORECASE))


def is_unresolved(location: str | None) -> bool:
    """True when we can't tell where a job is without fetching its details."""
    return not location or bool(_PLACEHOLDER.match(location))

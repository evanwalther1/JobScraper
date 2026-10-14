"""Radar name matching and company health labels (no network, no DB)."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.radar import _location, normalize_name
from app.routes_companies import health


@pytest.mark.parametrize("adzuna, tracked", [
    ("Micron Technology, Inc.", "Micron Technology"),
    ("CompuNet, Inc.", "CompuNet"),
    ("Schweitzer Engineering Laboratories ", "Schweitzer Engineering Laboratories"),
    ("Idaho Power Company", "Idaho Power"),
    ("The Boeing Company", "Boeing"),
    ("Akamai Technologies, Inc.", "Akamai"),
    ("AT&T", "AT and T"),
])
def test_names_normalize_to_same_key(adzuna, tracked):
    assert normalize_name(adzuna) == normalize_name(tracked)


def test_distinct_companies_stay_distinct():
    assert normalize_name("Idaho Power") != normalize_name("Idaho Central Credit Union")
    # A name that is only a suffix word isn't stripped to nothing.
    assert normalize_name("Systems") == "systems"


def test_adzuna_location_uses_city_and_state():
    result = {"location": {"area": ["US", "Idaho", "Ada County", "Meridian"], "display_name": "Meridian, Ada County"}}
    assert _location(result) == "Meridian, Idaho"  # passes the Treasure Valley filter; "Ada County" wouldn't


NOW = datetime.now(timezone.utc)


def _company(**kw):
    defaults = dict(last_error=None, last_error_at=None, last_success_at=NOW, last_in_area=5, last_closed=0)
    return SimpleNamespace(**{**defaults, **kw})


def test_health_ok():
    assert health(_company()).label == "OK"


def test_health_failing_when_error_is_newer_than_success():
    c = _company(last_error="HTTPStatusError 404", last_error_at=NOW, last_success_at=NOW - timedelta(days=1))
    assert health(c).css == "error"


def test_health_recovered_after_later_success():
    c = _company(last_error="old", last_error_at=NOW - timedelta(days=2), last_success_at=NOW)
    assert health(c).label == "OK"


def test_health_dropped_to_zero_is_a_warning():
    assert health(_company(last_in_area=0, last_closed=7)).label == "Dropped to 0"


def test_health_quiet_board_is_not_a_warning():
    assert health(_company(last_in_area=0, last_closed=0)).css == "muted"


def test_remote_detection():
    from app.radar import looks_remote
    assert looks_remote("Full-stack Engineer 4 (Remote-Eligible)", None)
    assert looks_remote("Software Engineer", "This position is 100% telework.")
    assert not looks_remote("Remotely Operated Vehicle Technician", None)  # whole words only
    assert not looks_remote("Software Engineer", "Join our Boise office.")

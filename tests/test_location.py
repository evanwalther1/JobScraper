import pytest

from app.location import in_area, is_unresolved


@pytest.mark.parametrize("location", [
    "Boise, ID - Main Site",   # Micron
    "Idaho - Boise",           # SEL
    "Office - Boise",          # Clearwater
    "Idaho - Nampa",
    "Meridian, ID 83642",      # CompuNet
    "Eagle, Idaho",
    "Idaho - Meridian",
    "Boise, Idaho, United States; Phoenix, Arizona, United States",
])
def test_in_area(location):
    assert in_area(location)


@pytest.mark.parametrize("location", [
    "Meridian, Mississippi",
    "Eagle, CO",
    "Garden City, Kansas",
    "Star, TX",
    "Idaho Falls, ID",
    "Idaho - Lewiston",
    "US Remote",
    "Seattle, WA",
    None,
])
def test_not_in_area(location):
    assert not in_area(location)


def test_unresolved():
    assert is_unresolved("3 Locations")
    assert is_unresolved(None)
    assert not is_unresolved("Boise, ID")

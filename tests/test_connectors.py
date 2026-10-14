"""Connector parsing logic, using trimmed copies of real API responses (no network)."""

from types import SimpleNamespace

from app.connectors.ukg import _location_text
from app.connectors.workday import WorkdayConnector


def _company(aliases=None):
    return SimpleNamespace(location_aliases=aliases, ats_slug="t/wd1/site")


def _facets(*facets):
    return {"facets": list(facets)}


def _facet(param, *descriptors):
    return {"facetParameter": param, "values": [{"descriptor": d, "id": f"id-{d}"} for d in descriptors]}


def _nested(*facets):
    return {"facetParameter": "locationMainGroup", "values": [dict(f) for f in facets]}


pick = WorkdayConnector(client=None)._area_facets


def test_workday_city_level_match():  # Micron style, nested under locationMainGroup
    page = _facets(_nested(_facet("locations", "Boise, ID - Main Site", "Manassas, VA", "Boise, ID - ID1")))
    assert pick(page, _company()) == {"locations": ["id-Boise, ID - Main Site", "id-Boise, ID - ID1"]}


def test_workday_state_fallback():  # KLA: no city filter, but a State/Province one
    page = _facets(_facet("Country Region", "United States of America"),
                   _facet("State_Province_Region", "California", "Idaho"))
    assert pick(page, _company()) == {"State_Province_Region": ["id-Idaho"]}


def test_workday_fine_filters_without_our_area_means_nothing_local():  # HP
    page = _facets(_nested(_facet("primaryLocation", "Palo Alto, CA", "Vancouver, WA")))
    assert pick(page, _company()) == {}


def test_workday_country_only_falls_back_to_client_side():  # Equifax: must not return {}
    page = _facets(_facet("locationCountry", "United States of America", "Brazil"))
    assert pick(page, _company()) is None


def test_workday_company_aliases():  # Blue Cross of Idaho: "Meridian - Main"
    page = _facets(_nested(_facet("locations", "Meridian - Main", "Montana - Home Office")))
    assert pick(page, _company(r"^Meridian - Main$")) == {"locations": ["id-Meridian - Main"]}


def test_ukg_location_text():
    locations = [
        {"Address": {"City": "Boise", "State": {"Code": "ID"}}},
        {"Address": {"City": "Boise", "State": {"Code": "ID"}}},  # duplicate collapses
        {"Address": {}, "LocalizedDescription": "Remote"},
    ]
    assert _location_text(locations) == "Boise, ID; Remote"

"""Connector parsing logic, using trimmed copies of real API responses (no network)."""

from types import SimpleNamespace

from app.connectors.avature import _fields
from app.connectors.base import inner_html
from app.connectors.breezy import BreezyConnector
from app.connectors.cats import CatsConnector
from app.connectors.eightfold import EightfoldConnector
from app.connectors.successfactors import _STATE_ZIP
from app.connectors.ukg import _location_text
from app.detect_ats import _scan
from app.location import in_area
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


def test_cats_parse():  # SkyIO, trimmed from app.catsone.com/portal?id=22329
    job = {
        "id": 16863594, "title": " Control Systems Engineer ", "type": "H",
        "url": "http://careers.skyio.com/careers/22329-SkyIO-Inc/jobs/16863594-Control-Systems-Engineer/",
        "location": {"city": "Boise", "state": "Idaho", "postal_code": ""},
        "remote_type": "", "category_name": "none", "department": None,
        "description": "<p>Automation team</p>", "date_posted": "2026-10-13T16:22:49+00:00",
    }
    p = CatsConnector(client=None)._parse(job)
    assert (p.external_id, p.title, p.location) == ("16863594", "Control Systems Engineer", "Boise, Idaho")
    assert p.url.startswith("https://careers.skyio.com/")
    assert p.employment_type == "Hire" and p.department is None and p.remote is None
    assert p.posted_at.year == 2026 and in_area(p.location)


def test_cats_remote_job():
    job = {"id": 1, "title": "Aveva MES Engineer", "url": "https://x/1", "type": "C2H",
           "location": {"city": "Remote", "state": "United States"}, "remote_type": "Fully remote"}
    p = CatsConnector(client=None)._parse(job)
    assert p.remote is True and p.employment_type == "Contract to hire" and not in_area(p.location)


def test_detect_cats_widget():
    html = '<div id="cats-portal-widget-22329"></div><script>cjw({"id":22329,"domain":"catsone.com"});</script>'
    assert _scan(html)[:2] == ("cats", "22329")


def test_inner_html_handles_nesting():  # Simplot / Breezy descriptions nest the same tag
    page = '<span itemprop="description"><span class="x"><p>Hi</p></span><b>!</b></span><span>after</span>'
    assert inner_html(page, 'itemprop="description"', tag="span") == '<span class="x"><p>Hi</p></span><b>!</b>'
    assert inner_html(page, "missing") is None


def test_successfactors_state_zip_from_url():  # careers.simplot.com sitemap URLs
    assert _STATE_ZIP.search("Plant-Operations-Intern-(Caldwell,-ID)-ID-83607-9221").groups() == ("ID", "83607")
    assert _STATE_ZIP.search("Packaging-Operator-Grand-Forks,-ND-ND-58203-0826").groups() == ("ND", "58203")


def test_avature_fields():  # careers.slhs.org job page, "General Information" block
    page = """
      <div class="article__content__view__field__label"> City </div>
      <div class="article__content__view__field__value"> Boise </div>
      <div class="article__content__view__field__label">State</div>
      <div class="article__content__view__field__value">ID</div>"""
    assert _fields(page) == {"City": "Boise", "State": "ID"}


def test_eightfold_shows_local_locations_first():  # Ericsson posts some jobs in 300+ cities
    job = {"id": 5, "name": "Senior Pre-Sales Solution Architect ", "positionUrl": "/careers/job/5",
           "locations": ["Akron,Ohio,United States", "Boise,Idaho,United States"], "postedTs": 1793069853}
    company = SimpleNamespace(ats_slug="jobs.ericsson.com/ericsson.com", location_aliases=None)
    p = EightfoldConnector(client=None)._parse(job, company)
    assert p.location == "Boise,Idaho,United States" and p.external_id == "ericsson.com:5"
    assert p.url == "https://jobs.ericsson.com/careers/job/5" and p.posted_at.year == 2026


def test_breezy_parse():  # velox.breezy.hr/json
    job = {"id": "e88a", "name": "Jr. SEO Analyst", "url": "https://velox.breezy.hr/p/e88a-jr-seo-analyst",
           "published_date": "2026-10-21T18:49:21.146Z", "type": {"name": "Full-Time"},
           "location": {"city": "Boise", "state": {"id": "ID"}, "name": "Boise, ID", "is_remote": True}}
    p = BreezyConnector(client=None)._parse(job)
    assert (p.location, p.employment_type, p.remote) == ("Boise, ID", "Full-Time", None)

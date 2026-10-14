"""Workday: the JSON API behind every *.myworkdayjobs.com career site.

Not officially documented. It's what the company's own careers page calls.

  List (paged, max 20 per page):
    POST https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs
  Detail (one per job, has the description):
    GET  https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}{externalPath}

companies.ats_slug is stored as "tenant/wdN/site", e.g. "micron/wd1/External".
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from app.connectors.base import Connector, JobPosting, guess_remote
from app.location import in_area
from app.models import Company, Source

PAGE_SIZE = 20  # Workday rejects larger pages
PAGE_WORKERS = 4  # list pages fetched in parallel once the total is known
IDAHO_STATE = {"idaho", "id", "us-id", "usa-id", "united states - idaho"}


def _parse_slug(company: Company) -> tuple[str, str, str]:
    tenant, wd, site = company.ats_slug.split("/")
    return tenant, wd, site


class WorkdayConnector(Connector):
    source = Source.WORKDAY

    def _api_base(self, company: Company) -> str:
        tenant, wd, site = _parse_slug(company)
        return f"https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}"

    def _page(self, company: Company, offset: int, facets: dict) -> dict:
        body = {"appliedFacets": facets, "limit": PAGE_SIZE, "offset": offset, "searchText": ""}
        return self.request("POST", f"{self._api_base(company)}/jobs", json=body).json()

    def _area_facets(self, first_page: dict, company: Company) -> dict | None:
        """Pick this board's location filter values that cover our area.

        Location filter IDs differ per company, but they show up in every list response
        (often nested under "locationMainGroup"). Preference order:
          1. city/site values in the area   ("Boise, ID - Main Site")  -> {"locations": [ids]}
          2. a state value of Idaho          (KLA's State/Province)    -> {"State_...": [id]}
          3. board has city- or state-level filters, none of ours      -> {}   (nothing local)
          4. no usable filters (or country only)                       -> None (filter client-side)
        """
        city_matches: dict[str, list[str]] = {}
        state_matches: dict[str, list[str]] = {}
        has_fine_facet = False  # city/site or state level; a country filter alone proves nothing
        stack = list(first_page.get("facets", []))
        while stack:
            facet = stack.pop()
            param = facet.get("facetParameter", "")
            kind = param.lower()
            for value in facet.get("values", []):
                if "values" in value:  # nested group
                    stack.append(value)
                    continue
                if "country" in kind or not any(k in kind for k in ("location", "state", "region", "city")):
                    continue
                has_fine_facet = True
                descriptor = value.get("descriptor") or ""
                if in_area(descriptor, company.location_aliases):
                    city_matches.setdefault(param, []).append(value["id"])
                elif descriptor.strip().lower() in IDAHO_STATE:
                    state_matches.setdefault(param, []).append(value["id"])
        # Different facet params are ANDed together by Workday, so apply only one.
        for matches in (city_matches, state_matches):
            if matches:
                param, ids = next(iter(matches.items()))
                return {param: ids}
        return {} if has_fine_facet else None

    def _parse_page(self, company: Company, data: dict) -> list[JobPosting]:
        tenant, wd, site = _parse_slug(company)
        postings = []
        for job in data.get("jobPostings", []):
            path = job.get("externalPath")
            if not path:  # occasional placeholder entries with no link; nothing to store
                continue
            req_id = path.rsplit("_", 1)[-1]
            location = job.get("locationsText")
            postings.append(
                JobPosting(
                    # Req IDs are only unique within a tenant, so prefix them.
                    external_id=f"{tenant}:{req_id}",
                    title=job["title"].strip(),
                    url=f"https://{tenant}.{wd}.myworkdayjobs.com/{site}{path}",
                    location=location,
                    remote=guess_remote(location),
                    raw={"list": job},
                )
            )
        return postings

    def fetch(self, company: Company) -> list[JobPosting]:
        first = self._page(company, 0, {})
        facets = self._area_facets(first, company)
        if facets == {}:
            return []  # none of this company's locations are in our area
        if facets:
            first = self._page(company, 0, facets)
        else:
            facets = {}

        # Quirk: only the first page reports the real total; later pages say 0.
        total = first.get("total", 0)
        offsets = range(PAGE_SIZE, total, PAGE_SIZE)
        with ThreadPoolExecutor(PAGE_WORKERS) as pool:
            pages = [first] + list(pool.map(lambda off: self._page(company, off, facets), offsets))
        return [posting for page in pages for posting in self._parse_page(company, page)]

    def fetch_details(self, company: Company, posting: JobPosting) -> JobPosting:
        path = posting.raw["list"]["externalPath"]
        data = self.request("GET", f"{self._api_base(company)}{path}").json()
        info = data.get("jobPostingInfo", {})

        locations = [info.get("location")] + (info.get("additionalLocations") or [])
        location = "; ".join(loc for loc in locations if loc) or posting.location
        remote_type = (info.get("remoteType") or "").lower()

        posting.location = location
        posting.remote = True if "remote" in remote_type else guess_remote(location)
        posting.employment_type = info.get("timeType")
        posting.description_html = info.get("jobDescription")
        posting.url = info.get("externalUrl") or posting.url
        if start := info.get("startDate"):
            posting.posted_at = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
        posting.raw = {"list": posting.raw["list"], "detail": info}
        return posting

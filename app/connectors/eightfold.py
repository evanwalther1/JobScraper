"""Eightfold PCS: the JSON API behind Eightfold career sites (jobs.ericsson.com, *.eightfold.ai, ...).

Not officially documented. It's what the career site itself calls; robots.txt allows /api/pcsx.

  Search (paged, 10 per page, filtered server-side by location):
    GET https://{host}/api/pcsx/search?domain={domain}&query=&location=Boise, ID&start={n}
  Detail (description):
    GET https://{host}/api/pcsx/position_details?position_id={id}&domain={domain}&hl=en

companies.ats_slug is "host/domain", e.g. "jobs.ericsson.com/ericsson.com" or
"plexus.eightfold.ai/plexus.com". The location search also returns nearby towns (Nampa)
and some fuzzy matches elsewhere; refresh's location filter drops those.
"""

from datetime import datetime, timezone

from app.connectors.base import Connector, JobPosting, guess_remote
from app.location import in_area
from app.models import Company, Source

SEARCH_LOCATION = "Boise, ID"
MAX_PAGES = 50  # 500 jobs near Boise would be far more than any company has


def _split_slug(company: Company) -> tuple[str, str]:
    host, domain = company.ats_slug.split("/")
    return host, domain


class EightfoldConnector(Connector):
    source = Source.EIGHTFOLD

    def fetch(self, company: Company) -> list[JobPosting]:
        host, domain = _split_slug(company)
        postings: list[JobPosting] = []
        start = 0
        for _ in range(MAX_PAGES):
            params = {"domain": domain, "query": "", "location": SEARCH_LOCATION, "start": start}
            data = self.request("GET", f"https://{host}/api/pcsx/search", params=params).json().get("data") or {}
            page = data.get("positions") or []
            postings += [self._parse(job, company) for job in page]
            start += len(page)
            if not page or start >= (data.get("count") or 0):
                break
        return postings

    def _parse(self, job: dict, company: Company) -> JobPosting:
        host, domain = _split_slug(company)
        # Some postings list hundreds of cities: show the local ones, not all of them.
        locations = job.get("locations") or []
        local = [loc for loc in locations if in_area(loc, company.location_aliases)]
        location = "; ".join(local or locations[:3]) or None
        posted = job.get("postedTs")
        return JobPosting(
            external_id=f"{domain}:{job['id']}",
            title=job["name"].strip(),
            url=f"https://{host}{job['positionUrl']}",
            location=location,
            remote=True if job.get("workLocationOption") == "remote" else guess_remote(location),
            department=job.get("department"),
            posted_at=datetime.fromtimestamp(posted, timezone.utc) if posted else None,
            raw={"list": job},
        )

    def fetch_details(self, company: Company, posting: JobPosting) -> JobPosting:
        host, domain = _split_slug(company)
        params = {"position_id": posting.external_id.split(":", 1)[1], "domain": domain, "hl": "en"}
        detail = self.request("GET", f"https://{host}/api/pcsx/position_details", params=params).json().get("data") or {}
        posting.description_html = detail.get("jobDescription") or None
        posting.raw = {"list": posting.raw["list"], "detail": detail}
        return posting

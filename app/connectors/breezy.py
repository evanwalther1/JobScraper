"""Breezy HR: the public JSON feed of {company}.breezy.hr job boards.

  List:   GET https://{slug}.breezy.hr/json       (structured location, no description)
  Detail: GET https://{slug}.breezy.hr/p/{friendly_id}   (description in <div class="description">)

companies.ats_slug is the subdomain, e.g. "velox".
"""

from datetime import datetime

from app.connectors.base import Connector, JobPosting, guess_remote, inner_html
from app.models import Company, Source


class BreezyConnector(Connector):
    source = Source.BREEZY

    def fetch(self, company: Company) -> list[JobPosting]:
        jobs = self.request("GET", f"https://{company.ats_slug}.breezy.hr/json").json()
        return [self._parse(job) for job in jobs]

    def _parse(self, job: dict) -> JobPosting:
        loc = job.get("location") or {}
        state = (loc.get("state") or {}).get("id")
        location = ", ".join(p for p in (loc.get("city"), state) if p) or loc.get("name")
        posted = job.get("published_date")
        return JobPosting(
            external_id=str(job["id"]),  # Breezy IDs are global
            title=job["name"].strip(),
            url=job["url"],
            location=location,
            # Breezy's is_remote also means "remote allowed", so only say remote when the location does.
            remote=guess_remote(location),
            department=job.get("department") or None,
            employment_type=(job.get("type") or {}).get("name"),
            posted_at=datetime.fromisoformat(posted.replace("Z", "+00:00")) if posted else None,
            raw=job,
        )

    def fetch_details(self, company: Company, posting: JobPosting) -> JobPosting:
        page = self.request("GET", posting.url).text
        posting.description_html = inner_html(page, 'class="description"')
        return posting

"""Greenhouse: documented public API, one request returns every job with its description.

GET https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs?content=true
"""

import html
from datetime import datetime

from app.connectors.base import Connector, JobPosting, guess_remote
from app.models import Company, Source


class GreenhouseConnector(Connector):
    source = Source.GREENHOUSE

    def fetch(self, company: Company) -> list[JobPosting]:
        url = f"https://boards-api.greenhouse.io/v1/boards/{company.ats_slug}/jobs"
        data = self.request("GET", url, params={"content": "true"}).json()
        return [self._parse(job) for job in data["jobs"]]

    def _parse(self, job: dict) -> JobPosting:
        location = (job.get("location") or {}).get("name")
        departments = job.get("departments") or []
        posted = job.get("first_published") or job.get("updated_at")
        return JobPosting(
            external_id=str(job["id"]),
            title=job["title"].strip(),
            url=job["absolute_url"],
            location=location,
            remote=guess_remote(location),
            department=departments[0]["name"] if departments else None,
            # Greenhouse double-encodes: content is HTML-escaped HTML.
            description_html=html.unescape(job.get("content") or "") or None,
            posted_at=datetime.fromisoformat(posted) if posted else None,
            raw=job,
        )

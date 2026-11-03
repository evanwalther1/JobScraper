"""CATS (catsone.com): the JSON behind CATS career portals and their embeddable job widget.

Not officially documented. It's what the widget on a company's careers page calls.
One request returns every job with its description:

  GET https://app.catsone.com/portal?id={portal_id}

companies.ats_slug is the portal ID, e.g. "22329" (SkyIO). Find it in the careers page
HTML as `cats-portal-widget-22329`, or in portal URLs as `/careers/22329-Company-Name`.
"""

from datetime import datetime

from app.connectors.base import Connector, JobPosting, guess_remote
from app.models import Company, Source

# CATS job order types.
_TYPES = {"H": "Hire", "C": "Contract", "C2H": "Contract to hire", "FL": "Freelance"}


def _location_text(location: dict | None) -> str | None:
    """{"city": "Boise", "state": "Idaho"} -> "Boise, Idaho"."""
    parts = [p for p in ((location or {}).get("city"), (location or {}).get("state")) if p]
    return ", ".join(parts) or None


class CatsConnector(Connector):
    source = Source.CATS

    def fetch(self, company: Company) -> list[JobPosting]:
        data = self.request("GET", "https://app.catsone.com/portal", params={"id": company.ats_slug}).json()
        return [self._parse(job) for job in data.get("jobs") or [] if not job.get("is_hidden")]

    def _parse(self, job: dict) -> JobPosting:
        location = _location_text(job.get("location"))
        category = job.get("category_name")
        return JobPosting(
            # Job order IDs are global across CATS, not per portal.
            external_id=str(job["id"]),
            title=job["title"].strip(),
            url=job["url"].replace("http://", "https://", 1),
            location=location,
            remote=True if job.get("remote_type") else guess_remote(location),
            department=job.get("department") or (category if category and category != "none" else None),
            employment_type=_TYPES.get(job.get("type"), job.get("type") or None),
            description_html=job.get("description") or None,
            posted_at=datetime.fromisoformat(job["date_posted"]) if job.get("date_posted") else None,
            raw=job,
        )

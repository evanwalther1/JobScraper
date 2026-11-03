"""BambooHR: the JSON behind {company}.bamboohr.com/careers job boards.

Not officially documented. It's what the careers page itself calls.

  List:   GET https://{slug}.bamboohr.com/careers/list          (structured city + state)
  Detail: GET https://{slug}.bamboohr.com/careers/{id}/detail   (description, date posted)

companies.ats_slug is the subdomain, e.g. "kellerassociates".
"""

from datetime import datetime, timezone

from app.connectors.base import Connector, JobPosting, guess_remote
from app.models import Company, Source


def _location_text(location: dict | None) -> str | None:
    parts = [p for p in ((location or {}).get("city"), (location or {}).get("state")) if p]
    return ", ".join(parts) or None


class BambooHRConnector(Connector):
    source = Source.BAMBOOHR

    def fetch(self, company: Company) -> list[JobPosting]:
        base = f"https://{company.ats_slug}.bamboohr.com/careers"
        jobs = self.request("GET", f"{base}/list").json().get("result") or []
        postings = []
        for job in jobs:
            location = _location_text(job.get("location")) or _location_text(job.get("atsLocation"))
            postings.append(
                JobPosting(
                    external_id=f"{company.ats_slug}:{job['id']}",  # IDs are per company
                    title=job["jobOpeningName"].strip(),
                    url=f"{base}/{job['id']}",
                    location=location,
                    # locationType "1" is remote; "0" on-site, "2" hybrid.
                    remote=True if job.get("isRemote") or job.get("locationType") == "1" else guess_remote(location),
                    department=job.get("departmentLabel"),
                    employment_type=job.get("employmentStatusLabel"),
                    raw={"list": job},
                )
            )
        return postings

    def fetch_details(self, company: Company, posting: JobPosting) -> JobPosting:
        job_id = posting.external_id.split(":", 1)[1]
        url = f"https://{company.ats_slug}.bamboohr.com/careers/{job_id}/detail"
        detail = (self.request("GET", url).json().get("result") or {}).get("jobOpening") or {}
        posting.description_html = detail.get("description") or None
        if detail.get("datePosted"):
            posting.posted_at = datetime.fromisoformat(detail["datePosted"]).replace(tzinfo=timezone.utc)
        posting.raw = {"list": posting.raw["list"], "detail": detail}
        return posting

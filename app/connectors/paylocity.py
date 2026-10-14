"""Paylocity: public job board pages at recruiting.paylocity.com.

  List: GET https://recruiting.paylocity.com/recruiting/jobs/All/{company_guid}
        The page embeds its data as `window.pageData = {...}` (jobs, addresses, short blurb).
  Detail: GET https://recruiting.paylocity.com/Recruiting/Jobs/Details/{job_id}
        Has a schema.org JobPosting (JSON-LD) block with the full description.

Paylocity also has a JSON feed (/recruiting/v2/api/feed/jobs/{guid}), but it only returns
jobs for companies that have turned the feed on, which none of ours have.

companies.ats_slug is the company GUID from the job board URL.
"""

import json
import re
from datetime import datetime

from app.connectors.base import Connector, JobPosting, guess_remote
from app.models import Company, Source

BASE = "https://recruiting.paylocity.com"
_PAGE_DATA = re.compile(r"window\.pageData\s*=\s*(\{.*?\});\s*</script>", re.S)
_JSON_LD = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.S)


class PaylocityConnector(Connector):
    source = Source.PAYLOCITY
    detail_workers = 2  # returns 429 Too Many Requests at 5 parallel

    def fetch(self, company: Company) -> list[JobPosting]:
        html = self.request("GET", f"{BASE}/recruiting/jobs/All/{company.ats_slug}").text
        match = _PAGE_DATA.search(html)
        if not match:
            raise ValueError("Paylocity page has no window.pageData (layout changed?)")
        data = json.loads(match.group(1))

        postings = []
        for job in data.get("Jobs") or []:
            if job.get("IsInternal"):
                continue
            loc = job.get("JobLocation") or {}
            city_state = ", ".join(p for p in (loc.get("City"), loc.get("State")) if p)
            location = city_state or job.get("LocationName")
            published = job.get("PublishedDate")
            postings.append(
                JobPosting(
                    external_id=str(job["JobId"]),  # Paylocity job IDs are global
                    title=job["JobTitle"].strip(),
                    url=f"{BASE}/Recruiting/Jobs/Details/{job['JobId']}",
                    location=location,
                    remote=True if job.get("IsRemote") else guess_remote(location),
                    department=job.get("HiringDepartment"),
                    posted_at=datetime.fromisoformat(published) if published else None,
                    raw={"list": job},
                )
            )
        return postings

    def fetch_details(self, company: Company, posting: JobPosting) -> JobPosting:
        html = self.request("GET", posting.url).text
        for block in _JSON_LD.findall(html):
            try:
                data = json.loads(block)
            except ValueError:
                continue
            if data.get("@type") == "JobPosting":
                posting.description_html = data.get("description")
                posting.raw = {"list": posting.raw["list"], "detail": data}
                break
        return posting

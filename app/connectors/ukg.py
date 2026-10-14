"""UKG Pro Recruiting (formerly UltiPro): the JSON API behind recruiting*.ultipro.com job boards.

Not officially documented. It's what the job board page itself calls.

  List (paged):
    POST https://{host}/{tenant}/JobBoard/{board}/JobBoardView/LoadSearchResults
  Detail (description is JSON embedded in the page as `CandidateOpportunityDetail({...})`):
    GET  https://{host}/{tenant}/JobBoard/{board}/OpportunityDetail?opportunityId={id}

companies.ats_slug is "host/tenant/board", e.g.
  "recruiting.ultipro.com/IDA1000/26bbbd4e-7790-a471-7482-a40a1f3f8f25"
"""

import json
from datetime import datetime

from app.connectors.base import Connector, JobPosting, guess_remote
from app.models import Company, Source

PAGE_SIZE = 50
_DETAIL_MARKER = "CandidateOpportunityDetail("


def _board_url(company: Company) -> str:
    host, tenant, board = company.ats_slug.split("/")
    return f"https://{host}/{tenant}/JobBoard/{board}"


def _location_text(locations: list[dict]) -> str | None:
    """Structured addresses -> "Boise, ID; Meridian, ID"."""
    parts = []
    for loc in locations or []:
        addr = loc.get("Address") or {}
        city = addr.get("City")
        state = (addr.get("State") or {}).get("Code")
        text = ", ".join(p for p in (city, state) if p) or loc.get("LocalizedDescription")
        if text and text not in parts:
            parts.append(text)
    return "; ".join(parts) or None


class UKGConnector(Connector):
    source = Source.UKG

    def fetch(self, company: Company) -> list[JobPosting]:
        base = _board_url(company)
        postings: list[JobPosting] = []
        skip, total = 0, None
        while total is None or skip < total:
            body = {"opportunitySearch": {"Top": PAGE_SIZE, "Skip": skip, "QueryString": "",
                                          "OrderBy": [], "Filters": []}}
            data = self.request("POST", f"{base}/JobBoardView/LoadSearchResults", json=body).json()
            total = data.get("totalCount", 0)
            page = data.get("opportunities", [])
            if not page:
                break
            for job in page:
                location = _location_text(job.get("Locations"))
                postings.append(
                    JobPosting(
                        external_id=f"{company.ats_slug.split('/')[1]}:{job['Id']}",
                        title=job["Title"].strip(),
                        url=f"{base}/OpportunityDetail?opportunityId={job['Id']}",
                        location=location,
                        remote=True if job.get("JobLocationType") == 2 else guess_remote(location),
                        department=job.get("JobCategoryName"),
                        employment_type="Full time" if job.get("FullTime") else "Part time",
                        posted_at=datetime.fromisoformat(job["PostedDate"]) if job.get("PostedDate") else None,
                        raw={"list": job},
                    )
                )
            skip += PAGE_SIZE
        return postings

    def fetch_details(self, company: Company, posting: JobPosting) -> JobPosting:
        html = self.request("GET", posting.url).text
        start = html.find(_DETAIL_MARKER)
        if start == -1:
            return posting
        # raw_decode parses one JSON value and ignores the rest of the page after it.
        detail, _ = json.JSONDecoder().raw_decode(html[start + len(_DETAIL_MARKER):])
        posting.description_html = detail.get("Description")
        posting.raw = {"list": posting.raw["list"], "detail": detail}
        return posting

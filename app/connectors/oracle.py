"""Oracle Recruiting Cloud: the REST API behind *.fa.*.oraclecloud.com candidate sites.

Not officially documented for public use. It's what the Candidate Experience site calls.

  Search:  GET https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions
             ?onlyData=true&expand=requisitionList.secondaryLocations
             &finder=findReqs;siteNumber={site},locationId={id},radius=30,radiusUnit=MI,limit=..,offset=..
  Detail:  GET .../recruitingCEJobRequisitionDetails?expand=all&onlyData=true
             &finder=ById;Id="{job_id}",siteNumber={site}

Server-side filtering: the first search returns location facets (top 100 locations by job
count) with Oracle location IDs. We take the company's Boise location as the anchor and search a
30-mile radius around it. That covers Meridian, Nampa, Caldwell, Eagle, Star, Kuna, and Emmett,
even when those towns are too small to appear among the top-100 facets.

companies.ats_slug is "host/siteNumber", e.g. "eofd.fa.us6.oraclecloud.com/CX_1".
"""

from datetime import datetime, timezone

from app.connectors.base import Connector, JobPosting, guess_remote
from app.location import in_area
from app.models import Company, Source

PAGE_SIZE = 200
RADIUS_MILES = 30
API = "hcmRestApi/resources/latest"


def _parse_slug(company: Company) -> tuple[str, str]:
    host, site = company.ats_slug.split("/")
    return host, site


class OracleConnector(Connector):
    source = Source.ORACLE

    def _search(self, company: Company, finder_extra: str, offset: int = 0, limit: int = PAGE_SIZE) -> dict:
        host, site = _parse_slug(company)
        finder = f"findReqs;siteNumber={site},facetsList=LOCATIONS{finder_extra},limit={limit},offset={offset},sortBy=POSTING_DATES_DESC"
        params = {"onlyData": "true", "expand": "requisitionList.secondaryLocations", "finder": finder}
        data = self.request("GET", f"https://{host}/{API}/recruitingCEJobRequisitions", params=params).json()
        return data["items"][0]

    def _anchor_location(self, company: Company) -> int | None:
        """Oracle location ID to center the radius search on: Boise, else any in-area location."""
        facets = self._search(company, "", limit=1).get("locationsFacet") or []
        local = [f for f in facets if in_area(f.get("Name"), company.location_aliases)]
        local.sort(key=lambda f: ("boise" not in f["Name"].lower(), -f.get("TotalCount", 0)))
        return local[0]["Id"] if local else None

    def fetch(self, company: Company) -> list[JobPosting]:
        host, site = _parse_slug(company)
        anchor = self._anchor_location(company)
        if anchor is None:
            return []  # none of this company's top locations are in our area
        area = f",locationId={anchor},radius={RADIUS_MILES},radiusUnit=MI"

        postings: list[JobPosting] = []
        offset, total = 0, None
        while total is None or offset < total:
            result = self._search(company, area, offset=offset)
            total = result.get("TotalJobsCount", 0)
            reqs = result.get("requisitionList") or []
            if not reqs:
                break
            for req in reqs:
                locations = [req.get("PrimaryLocation")] + [
                    s.get("Name") for s in req.get("secondaryLocations") or []
                ]
                location = "; ".join(dict.fromkeys(loc for loc in locations if loc)) or None
                posted = req.get("PostedDate")
                postings.append(
                    JobPosting(
                        # Requisition IDs are only unique within one Oracle instance.
                        external_id=f"{host.split('.')[0]}:{req['Id']}",
                        title=req["Title"].strip(),
                        url=f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{req['Id']}",
                        location=location,
                        remote=True if "remote" in (req.get("WorkplaceType") or "").lower() else guess_remote(location),
                        department=req.get("JobFamily") if req.get("JobFamily") not in (None, "None") else None,
                        employment_type=req.get("JobSchedule"),
                        posted_at=datetime.fromisoformat(posted).replace(tzinfo=timezone.utc) if posted else None,
                        raw={"list": req},
                    )
                )
            offset += PAGE_SIZE
        return postings

    def fetch_details(self, company: Company, posting: JobPosting) -> JobPosting:
        host, site = _parse_slug(company)
        job_id = posting.raw["list"]["Id"]
        params = {"expand": "all", "onlyData": "true", "finder": f'ById;Id="{job_id}",siteNumber={site}'}
        data = self.request("GET", f"https://{host}/{API}/recruitingCEJobRequisitionDetails", params=params).json()
        items = data.get("items") or []
        if not items:
            return posting
        detail = items[0]
        # Oracle splits the posting into sections; stitch them into one description.
        sections = [
            ("", detail.get("ExternalDescriptionStr")),
            ("Responsibilities", detail.get("ExternalResponsibilitiesStr")),
            ("Qualifications", detail.get("ExternalQualificationsStr")),
        ]
        html = "".join(
            (f"<h3>{heading}</h3>" if heading else "") + body for heading, body in sections if body
        )
        posting.description_html = html or None
        posting.raw = {"list": posting.raw["list"], "detail": detail}
        return posting

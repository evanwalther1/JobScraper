"""SAP SuccessFactors career sites ("Recruiting Marketing", e.g. careers.simplot.com).

Their JSON API lives under /services/, which robots.txt disallows. What they do publish for
crawlers is a sitemap of every open job, and plain job pages with schema.org markup:

  List:   GET https://{host}/sitemap.xml
          -> https://{host}/job/{Title-City,-ST-ST-ZIP-PLUS4}/{job_id}/
  Detail: GET https://{host}/job/.../{job_id}/   (itemprop title, description, address, datePosted)

companies.ats_slug is the career site host, e.g. "careers.simplot.com".

The URL ends with the job's state and ZIP code. Postings outside Idaho are dropped from that
alone; Idaho postings get their real city from the job page (refresh treats a missing
location as "check the details"), and the out-of-area ones are remembered in
excluded_postings so they're only checked once.
"""

import html
import re
from datetime import datetime, timezone
from urllib.parse import unquote

from app.connectors.base import Connector, JobPosting, inner_html
from app.models import Company, Source

_JOB_URL = re.compile(r"<loc>\s*(https://[^<]+/job/([^/<]+)/(\d+)/?)\s*</loc>")
_STATE_ZIP = re.compile(r"-([A-Z]{2})-(\d{5})(?:-\d{4})?$")


def _meta(page: str, prop: str) -> str | None:
    m = re.search(rf'itemprop="{prop}"\s+content="([^"]*)"', page)
    return html.unescape(m.group(1)).strip() if m else None


class SuccessFactorsConnector(Connector):
    source = Source.SUCCESSFACTORS

    def fetch(self, company: Company) -> list[JobPosting]:
        sitemap = self.request("GET", f"https://{company.ats_slug}/sitemap.xml").text
        postings = []
        for url, slug, job_id in _JOB_URL.findall(sitemap):
            slug = html.unescape(unquote(slug))
            m = _STATE_ZIP.search(slug)
            postings.append(
                JobPosting(
                    external_id=f"{company.ats_slug}:{job_id}",
                    title=slug.replace("-", " "),  # placeholder until the job page is read
                    url=html.unescape(url),
                    # Idaho: unknown city until we read the page. Elsewhere: out of area.
                    location=None if not m or m.group(1) == "ID" else f"{m.group(1)} {m.group(2)}",
                )
            )
        return postings

    def fetch_details(self, company: Company, posting: JobPosting) -> JobPosting:
        page = self.request("GET", posting.url).text
        title = re.search(r'itemprop="title"[^>]*>([^<]+)<', page)
        if title:
            posting.title = html.unescape(title.group(1)).strip()
        city, state = _meta(page, "addressLocality"), _meta(page, "addressRegion")
        posting.location = ", ".join(p for p in (city, state) if p) or None
        posting.description_html = inner_html(page, 'itemprop="description"', tag="span")
        posted = _meta(page, "datePosted")  # "Mon Oct 19 00:00:00 UTC 2026"
        if posted:
            try:
                posting.posted_at = datetime.strptime(posted, "%a %b %d %H:%M:%S UTC %Y").replace(tzinfo=timezone.utc)
            except ValueError:
                pass
        return posting

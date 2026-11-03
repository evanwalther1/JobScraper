"""Avature career portals (e.g. St. Luke's: careers.slhs.org/careersmarketplace).

No public JSON API. The portal publishes a sitemap of every job for crawlers, and each job
page lists labeled fields (City, State, Department, ...) plus the description:

  List:   GET https://{host}/{portal}/sitemap.xml   -> .../JobDetail/{Title-Slug}/{job_id}
  Detail: GET https://{host}/{portal}/JobDetail/{Title-Slug}/{job_id}

companies.ats_slug is "host/portal", e.g. "careers.slhs.org/careersmarketplace".

The sitemap has no locations, so the first refresh reads every job page once. After that,
stored jobs are skipped and out-of-area ones are remembered in excluded_postings.
Slugs can be stale (a renamed job keeps its old slug), so the title comes from the page.
"""

import html
import re

from app.connectors.base import Connector, JobPosting, inner_html
from app.models import Company, Source

_JOB_URL = re.compile(r"<loc>\s*(https://[^<]+/JobDetail/([^/<]+)/(\d+))\s*</loc>")
_FIELD = re.compile(
    r'article__content__view__field__label[^"]*"[^>]*>\s*(.*?)\s*</div>\s*'
    r'<div class="article__content__view__field__value[^"]*"[^>]*>\s*(.*?)\s*</div>',
    re.S,
)


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def _fields(page: str) -> dict[str, str]:
    """The "General Information" block: {"City": "Boise", "State": "ID", ...}."""
    return {_text(label): _text(value) for label, value in _FIELD.findall(page)}


class AvatureConnector(Connector):
    source = Source.AVATURE
    detail_workers = 3  # first run reads every job page; go easy on the portal

    def fetch(self, company: Company) -> list[JobPosting]:
        sitemap = self.request("GET", f"https://{company.ats_slug}/sitemap.xml").text
        return [
            JobPosting(
                external_id=f"{company.ats_slug.split('/')[0]}:{job_id}",
                title=slug.replace("-", " "),  # placeholder until the job page is read
                url=url,
                location=None,  # only the job page says where it is
            )
            for url, slug, job_id in _JOB_URL.findall(sitemap)
        ]

    def fetch_details(self, company: Company, posting: JobPosting) -> JobPosting:
        page = self.request("GET", posting.url).text
        title = re.search(r'property="og:title"\s+content="([^"]*)"', page)
        if title:
            # Some titles are escaped twice ("&amp;amp;"), so unescape twice.
            posting.title = html.unescape(html.unescape(title.group(1))).strip()
        fields = _fields(page)
        posting.location = ", ".join(p for p in (fields.get("City"), fields.get("State")) if p) or None
        posting.department = fields.get("Department") or fields.get("Category")
        heading = page.find("Description &amp; Requirements")
        if heading != -1:
            posting.description_html = inner_html(page, "article__content__view__field__value", start=heading)
        posting.raw = {"fields": fields}
        return posting

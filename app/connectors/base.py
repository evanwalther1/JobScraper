"""The connector contract every ATS implementation follows.

A connector turns one company's job board into a list of JobPostings,
our normalized shape. refresh.py doesn't care which ATS a posting came from.
"""

import hashlib
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx

from app.models import Company, Source


@dataclass
class JobPosting:
    external_id: str
    title: str
    url: str
    location: str | None = None
    remote: bool | None = None
    department: str | None = None
    employment_type: str | None = None
    description_html: str | None = None
    posted_at: datetime | None = None
    raw: dict[str, Any] | None = None

    def content_hash(self) -> str:
        """Changes when the posting's meaningful content changes."""
        parts = [self.title, self.location or "", self.description_html or ""]
        return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()


def guess_remote(location: str | None) -> bool | None:
    if location and "remote" in location.lower():
        return True
    return None


class Connector(ABC):
    source: Source
    # Parallel detail requests. Lower it for ATSes that rate-limit (HTTP 429).
    detail_workers: int = 5

    def __init__(self, client: httpx.Client):
        self.client = client

    @abstractmethod
    def fetch(self, company: Company) -> list[JobPosting]:
        """Return every open posting on the company's board.

        Descriptions may be left as None if the ATS needs a separate request per
        job; refresh calls fetch_details only for postings it hasn't seen before.
        """

    def fetch_details(self, company: Company, posting: JobPosting) -> JobPosting:
        """Fill in description and other per-job fields. Default: nothing to do."""
        return posting

    def request(self, method: str, url: str, retries: int = 4, **kwargs) -> httpx.Response:
        """HTTP with exponential backoff on rate limits (429) and server errors (5xx)."""
        for attempt in range(retries + 1):
            try:
                resp = self.client.request(method, url, **kwargs)
                if resp.status_code != 429 and resp.status_code < 500:
                    resp.raise_for_status()
                    return resp
            except httpx.TransportError:
                if attempt == retries:
                    raise
            if attempt < retries:
                time.sleep(2**attempt)  # 1s, 2s, 4s, 8s
        resp.raise_for_status()
        return resp

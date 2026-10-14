"""Shared web plumbing: templates, DB session dependency, and template helpers.

Used by app/main.py (jobs + applications pages) and the route modules for the
Companies (app/routes_companies.py) and Radar (app/routes_radar.py) pages.
"""

from datetime import datetime, timezone
from pathlib import Path

import nh3
from fastapi import HTTPException, Request
from fastapi.templating import Jinja2Templates

from app.db import SessionLocal
from app.models import Status

BASE = Path(__file__).parent
templates = Jinja2Templates(directory=BASE / "templates")

# Statuses shown on the Applications page, in pipeline order.
PIPELINE = [Status.SAVED, Status.APPLIED, Status.INTERVIEWING, Status.OFFER, Status.REJECTED, Status.WITHDRAWN]


def get_session():
    with SessionLocal() as session:
        yield session


def is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


def not_found():
    raise HTTPException(status_code=404)


def ago(dt: datetime | None) -> str:
    if dt is None:
        return ""
    delta = datetime.now(timezone.utc) - dt
    if delta.days <= 0:
        hours = int(delta.total_seconds() // 3600)
        if hours < 1:
            return "just now"
        return "today" if hours >= 12 else f"{hours}h ago"
    if delta.days == 1:
        return "yesterday"
    if delta.days < 30:
        return f"{delta.days} days ago"
    return dt.strftime("%b %d, %Y")


def safe_html(raw: str | None) -> str:
    """Job descriptions are third-party HTML: strip scripts, event handlers, styles, iframes."""
    return nh3.clean(raw or "", link_rel="noopener noreferrer nofollow")


templates.env.filters["ago"] = ago
templates.env.filters["safe_html"] = safe_html
templates.env.globals["ALL_STATUSES"] = PIPELINE + [Status.DISMISSED]

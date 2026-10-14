"""Companies page: everyone we poll, their health, and my preference (favorite / hidden)."""

from dataclasses import dataclass

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.connectors import CONNECTORS
from app.models import Company, Job, Preference, Relevance, Source
from app.refresh import run_refresh
from app.web import get_session, not_found, templates

router = APIRouter()

SUPPORTED_SOURCES = [s for s in Source if s in CONNECTORS]


@dataclass
class Health:
    label: str
    css: str  # ok | warn | error | muted
    detail: str = ""


def health(c: Company) -> Health:
    """Turn the last refresh's results into a status a human can act on."""
    if c.last_error and (c.last_success_at is None or c.last_error_at > c.last_success_at):
        return Health("Failing", "error", c.last_error[:300])
    if c.last_success_at is None:
        return Health("Not refreshed yet", "muted")
    if c.last_in_area == 0 and (c.last_closed or 0) > 0:
        # Every local job vanished at once: more often a broken board than a hiring freeze.
        return Health("Dropped to 0", "warn", f"{c.last_closed} jobs closed in the last refresh; check the careers page")
    if (c.last_closed or 0) >= 5 and c.last_closed > (c.last_in_area or 0):
        return Health("Many closed", "warn", f"{c.last_closed} closed vs {c.last_in_area} open in the last refresh")
    if c.last_in_area == 0:
        return Health("No local jobs", "muted", "Board works; nothing in the Treasure Valley right now")
    return Health("OK", "ok")


def _rows(session: Session, company_ids: list[int] | None = None) -> list[dict]:
    """Companies with their open-job and match counts."""
    counts = (
        select(
            Job.company_id,
            func.count().label("open"),
            func.count().filter(Job.relevance == Relevance.MATCH).label("matches"),
        )
        .where(Job.closed_at.is_(None))
        .group_by(Job.company_id)
        .subquery()
    )
    query = (
        select(Company, func.coalesce(counts.c.open, 0), func.coalesce(counts.c.matches, 0))
        .outerjoin(counts, counts.c.company_id == Company.id)
        .order_by(
            # favorites first, hidden last, then by how many matches they have
            (Company.preference == Preference.HIDDEN).asc(),
            (Company.preference == Preference.FAVORITE).desc(),
            func.coalesce(counts.c.matches, 0).desc(),
            Company.name,
        )
    )
    if company_ids is not None:
        query = query.where(Company.id.in_(company_ids))
    return [
        {"c": c, "open": open_, "matches": matches, "health": health(c)}
        for c, open_, matches in session.execute(query)
    ]


def _row_response(request: Request, session: Session, company_id: int):
    rows = _rows(session, [company_id])
    if not rows:
        not_found()
    return templates.TemplateResponse(request, "partials/company_row.html", {"row": rows[0]})


@router.get("/companies", response_class=HTMLResponse)
def companies_page(request: Request, session: Session = Depends(get_session)):
    rows = _rows(session)
    summary = {
        "total": len(rows),
        "favorites": sum(r["c"].preference == Preference.FAVORITE for r in rows),
        "hidden": sum(r["c"].preference == Preference.HIDDEN for r in rows),
        "attention": sum(r["health"].css in ("error", "warn") for r in rows),
    }
    return templates.TemplateResponse(
        request, "companies.html",
        {"rows": rows, "summary": summary, "sources": SUPPORTED_SOURCES, "prefill": {}},
    )


@router.post("/companies/{company_id}/preference", response_class=HTMLResponse)
def set_preference(
    request: Request, company_id: int, preference: Preference = Form(...), session: Session = Depends(get_session)
):
    company = session.get(Company, company_id) or not_found()
    company.preference = preference
    session.commit()
    return _row_response(request, session, company_id)


@router.post("/companies/{company_id}/notes", response_class=HTMLResponse)
def set_notes(request: Request, company_id: int, notes: str = Form(""), session: Session = Depends(get_session)):
    company = session.get(Company, company_id) or not_found()
    company.notes = notes.strip() or None
    session.commit()
    return HTMLResponse("")  # hx-swap="none": nothing on the page needs to change


@router.post("/companies/{company_id}/active", response_class=HTMLResponse)
def toggle_active(request: Request, company_id: int, session: Session = Depends(get_session)):
    company = session.get(Company, company_id) or not_found()
    company.active = not company.active
    session.commit()
    return _row_response(request, session, company_id)


@router.post("/companies/{company_id}/refresh", response_class=HTMLResponse)
def refresh_one(request: Request, company_id: int, session: Session = Depends(get_session)):
    run_refresh(company_ids=[company_id])
    session.expire_all()  # the refresh wrote through other sessions
    return _row_response(request, session, company_id)


@router.post("/companies", response_class=HTMLResponse)
def add_company(
    request: Request,
    name: str = Form(...),
    source: Source = Form(...),
    ats_slug: str = Form(...),
    careers_url: str = Form(""),
    location_aliases: str = Form(""),
    session: Session = Depends(get_session),
):
    """Add a company by hand, then refresh it right away so its jobs appear."""
    company = Company(
        name=name.strip(), source=source, ats_slug=ats_slug.strip(),
        careers_url=careers_url.strip() or None, location_aliases=location_aliases.strip() or None,
    )
    session.add(company)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        return HTMLResponse(
            f'<article class="form-error">{source.value} / {ats_slug} is already tracked.</article>'
        )
    result = run_refresh(company_ids=[company.id])[0]
    response = templates.TemplateResponse(request, "partials/company_added.html", {"name": company.name, "result": result})
    response.headers["HX-Trigger"] = "companies-changed"
    return response

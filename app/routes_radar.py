"""Radar page: companies hiring near Boise (per Adzuna) that we don't track, and coverage."""

from collections import defaultdict

import httpx
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.models import Company, RadarCompany, RadarPosting, Relevance, Source
from app.radar import SUPPORTED_ATS, coverage, detect_company, latest_run, run_radar
from app.refresh import run_refresh
from app.web import get_session, not_found, templates

router = APIRouter()


def _summaries(session: Session, keys: list[str] | None = None, ignored: bool = False) -> list[dict]:
    """Untracked radar companies with their local / relevant posting counts in the latest run."""
    run = latest_run(session)
    if run is None:
        return []
    query = (
        select(RadarCompany)
        .options(selectinload(RadarCompany.postings))
        .where(RadarCompany.company_id.is_(None), RadarCompany.ignored.is_(ignored))
    )
    if keys is not None:
        query = query.where(RadarCompany.key.in_(keys))
    rows = []
    for rc in session.scalars(query):
        local = [p for p in rc.postings if p.in_area and not p.remote and p.last_seen_at == run]
        if not local and keys is None:
            continue
        relevant = [p for p in local if p.relevance == Relevance.MATCH]
        rows.append({
            "rc": rc,
            "local": len(local),
            "relevant": len(relevant),
            "samples": (relevant or local)[:3],
            "supported": rc.detected_ats in SUPPORTED_ATS and bool(rc.detected_slug),
        })
    rows.sort(key=lambda r: (-r["relevant"], -r["local"], r["rc"].display_name.lower()))
    return rows


def _row(request: Request, session: Session, key: str):
    rows = _summaries(session, [key]) or _summaries(session, [key], ignored=True)
    if not rows:
        return HTMLResponse("")  # now tracked or gone: drop the row
    return templates.TemplateResponse(request, "partials/radar_row.html", {"r": rows[0]})


@router.get("/radar", response_class=HTMLResponse)
def radar_page(request: Request, show_all: bool = False, session: Session = Depends(get_session)):
    rows = _summaries(session)
    with_relevant = [r for r in rows if r["relevant"]]
    # Connector payoff: which unsupported ATSes would cover the most relevant postings?
    payoff: dict[str, dict] = defaultdict(lambda: {"companies": [], "relevant": 0})
    for r in with_relevant:
        ats = r["rc"].detected_ats
        if ats and not r["supported"]:
            payoff[ats]["companies"].append(r["rc"].display_name)
            payoff[ats]["relevant"] += r["relevant"]
    payoff_rows = sorted(payoff.items(), key=lambda kv: (-len(kv[1]["companies"]), -kv[1]["relevant"]))
    tracked = session.scalars(select(Company).order_by(Company.name)).all()
    return templates.TemplateResponse(request, "radar.html", {
        "cov": coverage(session),
        "rows": rows if show_all else with_relevant,
        "hidden_count": len(rows) - len(with_relevant),
        "show_all": show_all,
        "payoff": payoff_rows,
        "ignored": _summaries(session, ignored=True),
        "tracked": tracked,
    })


@router.post("/radar/run", response_class=HTMLResponse)
def radar_run():
    """Pull fresh Adzuna postings and detect new companies (takes a few minutes)."""
    run_radar()
    return HTMLResponse("", headers={"HX-Refresh": "true"})  # reload the page with new data


@router.post("/radar/ignore", response_class=HTMLResponse)
def radar_ignore(request: Request, key: str = Form(...), ignored: bool = Form(True), session: Session = Depends(get_session)):
    rc = session.get(RadarCompany, key) or not_found()
    rc.ignored = ignored
    session.commit()
    return HTMLResponse("")  # the row leaves the current list either way


@router.post("/radar/detect", response_class=HTMLResponse)
def radar_detect(request: Request, key: str = Form(...), domain: str = Form(""), session: Session = Depends(get_session)):
    """Re-run ATS detection, optionally with a corrected domain."""
    rc = session.get(RadarCompany, key) or not_found()
    domain = domain.strip().removeprefix("https://").removeprefix("http://").strip("/")
    rc.domain = domain or None
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) JobScraper-radar"}
    with httpx.Client(headers=headers, timeout=15, follow_redirects=True) as client:
        detect_company(rc, client)
    session.commit()
    return _row(request, session, key)


@router.post("/radar/link", response_class=HTMLResponse)
def radar_link(request: Request, key: str = Form(...), company_id: int = Form(...), session: Session = Depends(get_session)):
    """This radar company is one we already track under another name (e.g. Trinity Health)."""
    rc = session.get(RadarCompany, key) or not_found()
    rc.company_id = company_id
    session.commit()
    return HTMLResponse("")


@router.post("/radar/track", response_class=HTMLResponse)
def radar_track(request: Request, key: str = Form(...), session: Session = Depends(get_session)):
    """Start tracking a detected company: create it, link it, and refresh it right away."""
    rc = session.get(RadarCompany, key) or not_found()
    if rc.detected_ats not in SUPPORTED_ATS or not rc.detected_slug:
        not_found()
    company = Company(
        name=rc.display_name, source=Source(rc.detected_ats), ats_slug=rc.detected_slug,
        careers_url=f"https://{rc.domain}" if rc.domain else None,
    )
    session.add(company)
    try:
        session.flush()
    except IntegrityError:  # already tracked under another radar name: just link it
        session.rollback()
        rc = session.get(RadarCompany, key)
        company = session.scalar(select(Company).where(
            Company.source == rc.detected_ats, Company.ats_slug == rc.detected_slug))
    rc.company_id = company.id
    session.commit()
    result = run_refresh(company_ids=[company.id])[0]
    return templates.TemplateResponse(request, "partials/radar_tracked.html", {"name": company.name, "result": result})

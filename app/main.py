"""Web UI: FastAPI + Jinja templates + HTMX.

Run:  uvicorn app.main:app --reload      then open http://localhost:8000

Pages return full HTML. HTMX requests (filters, status buttons, notes) get back just the
fragment that changed, so the page updates in place without any JavaScript of our own.
"""

from collections import Counter
from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI, Form, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload

from app import routes_companies, routes_radar
from app.models import Application, Company, Job, Preference, Relevance, Status
from app.refresh import run_refresh
from app.web import BASE, PIPELINE, get_session, is_htmx, not_found, templates

app = FastAPI(title="JobScraper")
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
app.include_router(routes_companies.router)
app.include_router(routes_radar.router)

# A visit this long after the previous one starts a new "session" for NEW badges.
VISIT_GAP = timedelta(hours=1)


# --- Jobs page -----------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def jobs_page(
    request: Request,
    q: str = "",
    company: str = "",
    view: str = "match",  # match | senior | non_tech | all
    companies_view: str = "",  # "" = all but hidden companies | favorites | all
    hide_tracked: bool = False,
    session: Session = Depends(get_session),
):
    query = (
        select(Job)
        .options(joinedload(Job.application), joinedload(Job.company))
        .join(Company, Job.company_id == Company.id)
        .outerjoin(Application)
        .where(Job.closed_at.is_(None))
        # Newest-discovered day first; within a day, most recently posted first.
        .order_by(func.date_trunc("day", Job.first_seen_at).desc(), Job.posted_at.desc().nulls_last())
    )
    if view in {r.value for r in Relevance}:
        query = query.where(Job.relevance == view)
    if company:
        query = query.where(Job.company_name == company)  # picking a company shows it even if hidden
    elif companies_view == "favorites":
        query = query.where(Company.preference == Preference.FAVORITE)
    elif companies_view != "all":
        query = query.where(Company.preference != Preference.HIDDEN)
    if q:
        like = f"%{q}%"
        query = query.where(or_(Job.title.ilike(like), Job.location.ilike(like), Job.company_name.ilike(like)))
    # Dismissed jobs never show here; they live on the Applications page.
    query = query.where(or_(Application.status.is_(None), Application.status != Status.DISMISSED))
    if hide_tracked:
        query = query.where(Application.id.is_(None))
    jobs = session.scalars(query).unique().all()

    # NEW badges: jobs first seen since the previous visit. Cookies track the visit
    # boundary; filter requests (HTMX) don't move it, only full page loads.
    now = datetime.now(timezone.utc)
    last_visit = _cookie_time(request, "last_visit")
    new_since = _cookie_time(request, "new_since") or (now - timedelta(days=1))
    if not is_htmx(request) and last_visit and now - last_visit > VISIT_GAP:
        new_since = last_visit

    context = {
        "jobs": jobs,
        "new_since": new_since,
        "companies": session.execute(
            select(Company.name, Company.preference).where(Company.active).order_by(Company.name)
        ).all(),
        "filters": {"q": q, "company": company, "view": view, "companies_view": companies_view,
                    "hide_tracked": hide_tracked},
    }
    template = "partials/job_list.html" if is_htmx(request) else "jobs.html"
    response = templates.TemplateResponse(request, template, context)
    if not is_htmx(request):
        response.set_cookie("last_visit", now.isoformat(), max_age=60 * 60 * 24 * 365)
        response.set_cookie("new_since", new_since.isoformat(), max_age=60 * 60 * 24 * 365)
    return response


def _cookie_time(request: Request, name: str) -> datetime | None:
    try:
        return datetime.fromisoformat(request.cookies[name])
    except (KeyError, ValueError):
        return None


@app.get("/jobs/{job_id}/description", response_class=HTMLResponse)
def job_description(request: Request, job_id: int, session: Session = Depends(get_session)):
    job = session.get(Job, job_id) or not_found()
    return templates.TemplateResponse(request, "partials/description.html", {"job": job})


@app.post("/jobs/{job_id}/status", response_class=HTMLResponse)
def set_job_status(
    request: Request, job_id: int, status: Status = Form(...), session: Session = Depends(get_session)
):
    """Save / Applied / Dismiss buttons on the jobs page."""
    job = session.get(Job, job_id, options=[joinedload(Job.application)]) or not_found()
    _set_status(job, status)
    session.commit()
    if status == Status.DISMISSED:
        return Response("")  # the card swaps itself out of the list
    return templates.TemplateResponse(
        request, "partials/job_card.html", {"job": job, "new_since": datetime.now(timezone.utc)}
    )


def _set_status(job: Job, status: Status) -> None:
    if job.application is None:
        job.application = Application(status=status)
    else:
        job.application.status = status
    if status == Status.APPLIED and job.application.applied_at is None:
        job.application.applied_at = datetime.now(timezone.utc)


# --- Applications page ---------------------------------------------------------

@app.get("/applications", response_class=HTMLResponse)
def applications_page(request: Request, status: str = "", session: Session = Depends(get_session)):
    apps = session.scalars(
        select(Application)
        .options(joinedload(Application.job))
        .order_by(Application.updated_at.desc())
    ).all()
    counts = Counter(a.status for a in apps)
    if status:
        shown = [a for a in apps if a.status == status]
    else:
        shown = [a for a in apps if a.status != Status.DISMISSED]
    return templates.TemplateResponse(
        request,
        "applications.html",
        {"apps": shown, "counts": counts, "active_status": status, "total": sum(
            n for s, n in counts.items() if s != Status.DISMISSED)},
    )


@app.post("/applications/{app_id}", response_class=HTMLResponse)
def update_application(
    request: Request,
    app_id: int,
    status: Status | None = Form(None),
    notes: str | None = Form(None),
    applied_at: str | None = Form(None),
    session: Session = Depends(get_session),
):
    application = session.get(Application, app_id, options=[joinedload(Application.job)]) or not_found()
    if status is not None:
        _set_status(application.job, status)
    if notes is not None:
        application.notes = notes.strip() or None
    if applied_at is not None:
        application.applied_at = (
            datetime.fromisoformat(applied_at).replace(tzinfo=timezone.utc) if applied_at else None
        )
    session.commit()
    return templates.TemplateResponse(request, "partials/application_row.html", {"a": application})


@app.delete("/applications/{app_id}", response_class=HTMLResponse)
def delete_application(app_id: int, session: Session = Depends(get_session)):
    """Stop tracking a job (it goes back to the jobs list)."""
    application = session.get(Application, app_id) or not_found()
    session.delete(application)
    session.commit()
    return Response("")


# --- Refresh ---------------------------------------------------------------------

@app.post("/refresh", response_class=HTMLResponse)
def refresh(request: Request):
    """Runs the same refresh as `python -m app.refresh` (~20s)."""
    results = run_refresh()
    ok = [r for r in results if r["ok"]]
    summary = {
        "ok": len(ok),
        "failed": len(results) - len(ok),
        "new": sum(r["new"] for r in ok),
        "closed": sum(r["closed"] for r in ok),
    }
    response = templates.TemplateResponse(
        request, "partials/refresh_result.html", {"results": results, "summary": summary}
    )
    response.headers["HX-Trigger"] = "jobs-changed"  # tells the job list to reload itself
    return response
